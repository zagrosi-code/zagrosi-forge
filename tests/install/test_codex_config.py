from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Iterator

import pytest


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/package/config"
PROTECTED_FIXTURES = json.loads(
    (FIXTURES / "expected/protected-spans.json").read_text(encoding="utf-8")
)


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


def _identity(*, rendered: str = "d" * 64, base: str = "c" * 64):
    from zagrosi_forge.install.contracts import InstallIdentity
    from zagrosi_forge.install.version import derive_install_version

    return InstallIdentity(
        marketplace_id="zagrosi",
        plugin_id="zagrosi-forge",
        base_version="0.2.0",
        install_version=derive_install_version("0.2.0", base),
        base_payload_digest=base,
        rendered_payload_digest=rendered,
        policy_digest="e" * 64,
        transformation_profile="plugin-v1",
        contract_versions=("finding-v1", "identity-v1"),
    )


def _reference(raw: str):
    from zagrosi_forge.install.paths import validate_reference
    from zagrosi_forge.install.policies import LIMIT_POLICY

    return validate_reference(raw, role="config-test", limits=LIMIT_POLICY).unwrap()


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


def _write_private_test_file(directory: Path, name: str, raw: bytes) -> Path:
    path = directory / name
    if os.name != "nt":
        path.write_bytes(raw)
        path.chmod(0o600)
        return path
    import zagrosi_forge.install.paths as paths

    parent = paths._windows_open_path(os.fspath(directory))
    descriptor = 0
    try:
        descriptor = paths._windows_create_private_file(parent, name)
        paths._windows_write(descriptor, raw)
    finally:
        if descriptor:
            paths._windows_close(descriptor)
        paths._windows_close(parent)
    return path


def _source_relative(identity: Any, effective_id: str = "zagrosi") -> str:
    return (
        f"sources/{effective_id}/zagrosi-forge/{identity.install_version}/marketplace"
    )


def _cache_relative(identity: Any, effective_id: str = "zagrosi") -> str:
    return f"cache/{effective_id}/zagrosi-forge/{identity.install_version}"


def _fixture(relative: str, **replacements: str) -> bytes:
    rendered = (FIXTURES / relative).read_text(encoding="utf-8")
    for key, value in replacements.items():
        escaped = json.dumps(value, ensure_ascii=False)[1:-1]
        rendered = rendered.replace("${" + key + "}", escaped)
    return rendered.encode()


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _set_test_xattr(path: Path, name: bytes, value: bytes) -> None:
    setter = getattr(os, "setxattr", None)
    if setter is not None:
        setter(path, name, value)
        return
    subprocess.run(
        [
            "/usr/bin/xattr",
            "-w",
            name.decode("ascii"),
            value.decode("ascii"),
            os.fspath(path),
        ],
        check=True,
        capture_output=True,
    )


def _get_test_xattr(path: Path, name: bytes) -> bytes:
    getter = getattr(os, "getxattr", None)
    if getter is not None:
        return getter(path, name)
    observed = subprocess.run(
        [
            "/usr/bin/xattr",
            "-px",
            name.decode("ascii"),
            os.fspath(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return bytes.fromhex(observed.stdout)


@contextmanager
def _config_context(
    tmp_path: Path,
    raw: bytes | None,
    *,
    identity: Any | None = None,
    mode: int = 0o600,
    xattrs: dict[bytes, bytes] | None = None,
) -> Iterator[tuple[Any, Any, Any, Any, Path, Any, Any]]:
    from zagrosi_forge.install.config import snapshot_config
    from zagrosi_forge.install.paths import PlatformPathAuthority

    candidate = _identity() if identity is None else identity
    home = tmp_path / "codex-home"
    _private_directory(home)
    if raw is not None:
        config = _write_private_test_file(home, "config.toml", raw)
        if os.name != "nt":
            config.chmod(mode)
            for name, value in (xattrs or {}).items():
                _set_test_xattr(config, name, value)
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    planned = authority.plan_owned_path(
        root,
        _reference(_source_relative(candidate)),
        expected_depth=5,
    ).unwrap()
    snapshot = snapshot_config(proof).unwrap()
    try:
        yield snapshot, candidate, planned, proof, home, root, authority
    finally:
        planned.close()
        proof.close()
        root.close()


def _receipt_record(
    identity: Any,
    raw_config: bytes,
    source_digest: str,
    cache_digest: str,
    *,
    effective_id: str = "zagrosi",
    config_after_digest: str | None = None,
):
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
        "effective_marketplace_id": effective_id,
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
        "transaction": {"id": "tx-config", "lineage": ["tx-config"]},
        "source": {
            "relative_path": _source_relative(identity, effective_id),
            "manifest_digest": source_digest,
        },
        "cache": {
            "relative_path": _cache_relative(identity, effective_id),
            "manifest_digest": cache_digest,
        },
        "config": {
            "path_id": "codex-config",
            "before_digest": "0" * 64,
            "after_digest": (
                hashlib.sha256(raw_config).hexdigest()
                if config_after_digest is None
                else config_after_digest
            ),
        },
        "tools": {
            "installer_version": "0.2.0",
            "python_version": "3.11.0",
            "codex_version": "0.1.0",
            "platform": "linux",
            "verifier_version": "1.0.0",
        },
        "created_at": "2026-07-16T00:00:00Z",
    }


def _record_bytes(record: dict[str, object]) -> bytes:
    from zagrosi_forge.install.contracts import canonical_json_bytes

    payload = copy.deepcopy(record)
    payload["record_digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return canonical_json_bytes(payload, final_newline=True)


def _make_relation(
    authority: Any,
    root: Any,
    home: Path,
    identity: Any,
    raw: bytes,
    *,
    effective_id: str = "zagrosi",
    config_after_digest: str | None = None,
):
    from zagrosi_forge.install.ownership import (
        committed_receipt_reference,
        observe_generation_identity,
        publish_committed_receipt,
        validate_active_install_relation,
    )

    plugins = home / "plugins"
    source_relative = _source_relative(identity, effective_id)
    cache_relative = _cache_relative(identity, effective_id)
    source_manifest_relative = (
        f"{source_relative}/plugins/zagrosi-forge/.codex-plugin/bundle-manifest.json"
    )
    cache_manifest_relative = f"{cache_relative}/.codex-plugin/bundle-manifest.json"
    for relative in (source_relative, cache_relative):
        path = plugins / relative
        path.mkdir(parents=True, mode=0o700)
    source_manifest = plugins / source_manifest_relative
    cache_manifest = plugins / cache_manifest_relative
    source_manifest.parent.mkdir(parents=True, exist_ok=True)
    cache_manifest.parent.mkdir(parents=True, exist_ok=True)
    source_manifest.write_bytes(b"source-manifest\n")
    cache_manifest.write_bytes(b"cache-manifest\n")
    source_path = authority.prove_descendant(
        root, _reference(source_relative), expected_depth=5
    ).unwrap()
    cache_path = authority.prove_descendant(
        root, _reference(cache_relative), expected_depth=4
    ).unwrap()
    manifest_root = authority.open_source_root(plugins)
    opened_source = manifest_root.open_regular_file(
        _reference(source_manifest_relative)
    )
    opened_cache = manifest_root.open_regular_file(_reference(cache_manifest_relative))
    source = observe_generation_identity(
        effective_marketplace_id=effective_id,
        root_role="source",
        identity=identity,
        path=source_path,
        manifest=opened_source,
    ).unwrap()
    cache = observe_generation_identity(
        effective_marketplace_id=effective_id,
        root_role="cache",
        identity=identity,
        path=cache_path,
        manifest=opened_cache,
    ).unwrap()
    receipt = _receipt_record(
        identity,
        raw,
        source.manifest_digest,
        cache.manifest_digest,
        effective_id=effective_id,
        config_after_digest=config_after_digest,
    )
    publish_committed_receipt(root, raw=_record_bytes(receipt)).unwrap()
    receipt_root = authority.open_source_root(plugins)
    opened_receipt = receipt_root.open_regular_file(
        committed_receipt_reference(effective_id, identity)
    )
    relation = validate_active_install_relation(
        opened_receipt,
        owned_root=root,
        source=source,
        cache=cache,
    ).unwrap()
    resources = (
        opened_receipt,
        receipt_root,
        opened_source,
        opened_cache,
        manifest_root,
        source_path,
        cache_path,
    )
    return relation, resources


def _close_all(resources: tuple[Any, ...]) -> None:
    for resource in resources:
        resource.close()


def _code(result: Any) -> str:
    assert not result.is_ok
    assert result.error is not None
    assert result.error.exit_category == 13
    return result.error.code


def _commit_acknowledged(prepared: Any, *, expected: Any) -> Any:
    import zagrosi_forge.install.atomic_file as atomic_file

    acknowledged = atomic_file.acknowledge_config_preparation(prepared)
    if not acknowledged.is_ok:
        return acknowledged
    return atomic_file.commit_atomic_candidate(prepared, expected=expected)


def test_fixture_path_substitutions_are_valid_toml_on_windows() -> None:
    import tomllib

    windows_path = r"C:\Users\Ada\AppData\Local\zagrosi-forge"
    raw = _fixture("legacy/exact.toml", CHECKOUT=windows_path)
    assert tomllib.loads(raw.decode())["marketplaces"]["zagrosi"]["source"] == (
        windows_path
    )


def test_managed_projection_contains_only_three_v1_nodes(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import CollisionPolicy, plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    with _config_context(tmp_path, None) as (snapshot, identity, source, *_rest):
        plan = plan_config(
            snapshot,
            identity,
            source,
            receipt=None,
            legacy=load_legacy_install_catalog().unwrap(),
            collision_policy=CollisionPolicy.REJECT,
        ).unwrap()
        assert len(plan.managed_nodes) == 3
        assert {node.pointer for node in plan.managed_nodes} == {
            ("marketplaces", "zagrosi", "source_type"),
            ("marketplaces", "zagrosi", "source"),
            ("plugins", "zagrosi-forge@zagrosi", "enabled"),
        }


def test_missing_config_plans_add_without_write(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import (
        ConfigClassification,
        ConfigOperation,
        plan_config,
    )
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    with _config_context(tmp_path, None) as (
        snapshot,
        identity,
        source,
        _proof,
        home,
        _root,
        _authority,
    ):
        before = list(home.iterdir())
        plan = plan_config(
            snapshot,
            identity,
            source,
            receipt=None,
            legacy=load_legacy_install_catalog().unwrap(),
        ).unwrap()
        assert plan.classification is ConfigClassification.ABSENT
        assert plan.operation is ConfigOperation.ADD
        assert not (home / "config.toml").exists()
        assert list(home.iterdir()) == before


def test_missing_and_existing_empty_config_are_distinct_snapshots(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import (
        ConfigClassification,
        ConfigOperation,
        plan_config,
    )
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    missing_base = tmp_path / "missing"
    empty_base = tmp_path / "empty"
    missing_base.mkdir()
    empty_base.mkdir()
    with _config_context(missing_base, None) as (
        missing,
        identity,
        source,
        *_rest,
    ):
        missing_plan = plan_config(
            missing,
            identity,
            source,
            receipt=None,
            legacy=load_legacy_install_catalog().unwrap(),
        ).unwrap()
        assert not missing.present
        assert missing.leaf_identity is None
        assert missing_plan.classification is ConfigClassification.ABSENT
        assert missing_plan.operation is ConfigOperation.ADD
    with _config_context(empty_base, b"") as (
        empty,
        identity,
        source,
        _proof,
        home,
        *_rest,
    ):
        empty_plan = plan_config(
            empty,
            identity,
            source,
            receipt=None,
            legacy=load_legacy_install_catalog().unwrap(),
        ).unwrap()
        assert empty.present
        assert empty.leaf_identity is not None
        assert empty.byte_digest == missing.byte_digest
        assert empty.semantic_digest == missing.semantic_digest
        assert empty.snapshot_digest != missing.snapshot_digest
        assert empty_plan.classification is ConfigClassification.ABSENT
        assert empty_plan.operation is ConfigOperation.ADD
        assert (home / "config.toml").read_bytes() == b""


def test_receipt_proven_config_preserves_extra_user_keys(tmp_path: Path) -> None:
    import tomllib

    from zagrosi_forge.install.config import (
        ConfigClassification,
        _candidate_bytes,
        _without_managed,
        plan_config,
        render_config_candidate,
    )
    from zagrosi_forge.install.ownership import load_legacy_install_catalog
    from zagrosi_forge.install.paths import PlatformPathAuthority

    identity = _identity()
    home = tmp_path / "codex-home"
    _private_directory(home)
    source = os.fspath(home / "plugins" / Path(_source_relative(identity)))
    raw = _fixture("cases/managed-extra.toml", SOURCE=source)
    _write_private_test_file(home, "config.toml", raw)
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    planned = authority.plan_owned_path(
        root, _reference(_source_relative(identity)), expected_depth=5
    ).unwrap()
    resources: tuple[Any, ...] = ()
    try:
        from zagrosi_forge.install.config import snapshot_config

        snapshot = snapshot_config(proof).unwrap()
        relation, resources = _make_relation(
            authority,
            root,
            home,
            identity,
            raw,
            config_after_digest=snapshot.snapshot_digest,
        )
        plan = plan_config(
            snapshot,
            identity,
            planned,
            receipt=relation,
            legacy=load_legacy_install_catalog().unwrap(),
        ).unwrap()
        assert plan.classification is ConfigClassification.EXACT_MANAGED
        rendered = render_config_candidate(snapshot, plan).unwrap()
        assert _candidate_bytes(rendered) == raw
        assert b'user_note = "preserve me"' in _candidate_bytes(rendered)
        assert b"user_flag = 17" in _candidate_bytes(rendered)
        declaration = PROTECTED_FIXTURES["cases/managed-extra.toml"]
        assert (
            _without_managed(tomllib.loads(raw.decode()), "zagrosi")
            == declaration["expected_unmanaged"]
        )
        for span in declaration["protected_spans"]:
            assert raw.count(span.encode()) == 1
    finally:
        planned.close()
        proof.close()
        _close_all(resources)
        root.close()


def test_receipt_proven_update_preserves_unrelated_tables_and_representation(
    tmp_path: Path,
) -> None:
    import tomllib

    from zagrosi_forge.install.config import (
        ConfigOperation,
        _candidate_bytes,
        _without_managed,
        plan_config,
        render_config_candidate,
        snapshot_config,
    )
    from zagrosi_forge.install.ownership import load_legacy_install_catalog
    from zagrosi_forge.install.paths import PlatformPathAuthority

    previous = _identity(base="c" * 64)
    candidate_identity = _identity(base="f" * 64)
    home = tmp_path / "codex-home"
    _private_directory(home)
    old_source = os.fspath(home / "plugins" / Path(_source_relative(previous)))
    raw = _fixture("cases/managed-update.toml", SOURCE=old_source)
    _write_private_test_file(home, "config.toml", raw)
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    planned = authority.plan_owned_path(
        root,
        _reference(_source_relative(candidate_identity)),
        expected_depth=5,
    ).unwrap()
    resources: tuple[Any, ...] = ()
    try:
        snapshot = snapshot_config(proof).unwrap()
        relation, resources = _make_relation(
            authority,
            root,
            home,
            previous,
            raw,
            config_after_digest=snapshot.snapshot_digest,
        )
        plan = plan_config(
            snapshot,
            candidate_identity,
            planned,
            receipt=relation,
            legacy=load_legacy_install_catalog().unwrap(),
        ).unwrap()
        assert plan.operation is ConfigOperation.UPDATE_OWNED
        rendered = render_config_candidate(snapshot, plan).unwrap()
        candidate_raw = _candidate_bytes(rendered)
        assert candidate_raw != raw
        declaration = PROTECTED_FIXTURES["cases/managed-update.toml"]
        assert (
            _without_managed(tomllib.loads(candidate_raw.decode()), "zagrosi")
            == declaration["expected_unmanaged"]
        )
        for span in declaration["protected_spans"]:
            encoded = span.encode()
            assert raw.count(encoded) == candidate_raw.count(encoded) == 1
        repeated = render_config_candidate(snapshot, plan).unwrap()
        assert _candidate_bytes(repeated) == candidate_raw
    finally:
        planned.close()
        proof.close()
        _close_all(resources)
        root.close()


def test_receipt_snapshot_digest_mismatch_is_unmanaged_collision(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import plan_config, snapshot_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog
    from zagrosi_forge.install.paths import PlatformPathAuthority

    identity = _identity()
    home = tmp_path / "codex-home"
    _private_directory(home)
    source_value = os.fspath(home / "plugins" / Path(_source_relative(identity)))
    raw = _fixture("cases/managed-extra.toml", SOURCE=source_value)
    _write_private_test_file(home, "config.toml", raw)
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    planned = authority.plan_owned_path(
        root,
        _reference(_source_relative(identity)),
        expected_depth=5,
    ).unwrap()
    resources: tuple[Any, ...] = ()
    try:
        snapshot = snapshot_config(proof).unwrap()
        relation, resources = _make_relation(
            authority,
            root,
            home,
            identity,
            raw,
            config_after_digest="0" * 64,
        )
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    planned,
                    receipt=relation,
                    legacy=load_legacy_install_catalog().unwrap(),
                )
            )
            == "config.owner_collision"
        )
    finally:
        planned.close()
        proof.close()
        _close_all(resources)
        root.close()


def test_receipt_proven_alternate_effective_id_is_exact_managed(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import (
        ConfigClassification,
        _candidate_bytes,
        plan_config,
        render_config_candidate,
        snapshot_config,
    )
    from zagrosi_forge.install.contracts import install_identity_digest
    from zagrosi_forge.install.ownership import load_legacy_install_catalog
    from zagrosi_forge.install.paths import PlatformPathAuthority

    identity = _identity()
    effective_id = "zagrosi-local-" + install_identity_digest(identity)[:24]
    home = tmp_path / "codex-home"
    _private_directory(home)
    source_value = os.fspath(
        home / "plugins" / Path(_source_relative(identity, effective_id))
    )
    raw = (
        f"[marketplaces.{effective_id}]\n"
        'source_type = "local"\n'
        f"source = {_toml_string(source_value)}\n\n"
        f'[plugins."zagrosi-forge@{effective_id}"]\n'
        "enabled = true\n"
        'user_key = "preserve"\n'
    ).encode()
    _write_private_test_file(home, "config.toml", raw)
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    planned = authority.plan_owned_path(
        root,
        _reference(_source_relative(identity, effective_id)),
        expected_depth=5,
    ).unwrap()
    resources: tuple[Any, ...] = ()
    try:
        snapshot = snapshot_config(proof).unwrap()
        relation, resources = _make_relation(
            authority,
            root,
            home,
            identity,
            raw,
            effective_id=effective_id,
            config_after_digest=snapshot.snapshot_digest,
        )
        plan = plan_config(
            snapshot,
            identity,
            planned,
            receipt=relation,
            legacy=load_legacy_install_catalog().unwrap(),
        ).unwrap()
        assert plan.classification is ConfigClassification.EXACT_MANAGED
        assert plan.effective_marketplace_id == effective_id
        assert _candidate_bytes(render_config_candidate(snapshot, plan).unwrap()) == raw
    finally:
        planned.close()
        proof.close()
        _close_all(resources)
        root.close()


def test_receipt_proven_alternate_id_survives_update_and_next_run(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import (
        ConfigOperation,
        _candidate_bytes,
        plan_config,
        render_config_candidate,
        snapshot_config,
    )
    from zagrosi_forge.install.contracts import install_identity_digest
    from zagrosi_forge.install.ownership import load_legacy_install_catalog
    from zagrosi_forge.install.paths import PlatformPathAuthority

    initial = _identity(base="c" * 64)
    updated = _identity(base="f" * 64)
    effective_id = "zagrosi-local-" + install_identity_digest(initial)[:24]
    home = tmp_path / "codex-home"
    _private_directory(home)
    initial_source = os.fspath(
        home / "plugins" / Path(_source_relative(initial, effective_id))
    )
    raw = (
        f"[marketplaces.{effective_id}]\n"
        'source_type = "local"\n'
        f"source = {_toml_string(initial_source)}\n\n"
        f'[plugins."zagrosi-forge@{effective_id}"]\n'
        "enabled = true\n"
    ).encode()
    config = _write_private_test_file(home, "config.toml", raw)
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    legacy = load_legacy_install_catalog().unwrap()

    first_resources: tuple[Any, ...] = ()
    first_proof = authority.prove_config_path(root).unwrap()
    first_source = authority.plan_owned_path(
        root,
        _reference(_source_relative(updated, effective_id)),
        expected_depth=5,
    ).unwrap()
    try:
        first_snapshot = snapshot_config(first_proof).unwrap()
        first_relation, first_resources = _make_relation(
            authority,
            root,
            home,
            initial,
            raw,
            effective_id=effective_id,
            config_after_digest=first_snapshot.snapshot_digest,
        )
        update = plan_config(
            first_snapshot,
            updated,
            first_source,
            receipt=first_relation,
            legacy=legacy,
        ).unwrap()
        assert update.operation is ConfigOperation.UPDATE_OWNED
        assert update.effective_marketplace_id == effective_id
        updated_raw = _candidate_bytes(
            render_config_candidate(first_snapshot, update).unwrap()
        )
    finally:
        first_source.close()
        first_proof.close()
        _close_all(first_resources)

    config.write_bytes(updated_raw)
    if os.name != "nt":
        config.chmod(0o600)
    next_resources: tuple[Any, ...] = ()
    next_proof = authority.prove_config_path(root).unwrap()
    next_source = authority.plan_owned_path(
        root,
        _reference(_source_relative(updated, effective_id)),
        expected_depth=5,
    ).unwrap()
    try:
        next_snapshot = snapshot_config(next_proof).unwrap()
        next_relation, next_resources = _make_relation(
            authority,
            root,
            home,
            updated,
            updated_raw,
            effective_id=effective_id,
            config_after_digest=next_snapshot.snapshot_digest,
        )
        repeated = plan_config(
            next_snapshot,
            updated,
            next_source,
            receipt=next_relation,
            legacy=legacy,
        ).unwrap()
        assert repeated.operation is ConfigOperation.NO_OP
        assert repeated.effective_marketplace_id == effective_id
    finally:
        next_source.close()
        next_proof.close()
        _close_all(next_resources)
        root.close()


def test_exact_audited_legacy_requires_adoption_token(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import create_adoption_token, plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    checkout = tmp_path / "checkout/zagrosi-forge"
    checkout.mkdir(parents=True)
    raw = _fixture("legacy/exact.toml", CHECKOUT=os.fspath(checkout))
    legacy = load_legacy_install_catalog().unwrap()
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        assert _code(
            plan_config(snapshot, identity, source, receipt=None, legacy=legacy)
        ) == ("config.adoption_required")
        token = create_adoption_token(
            snapshot, identity, source, legacy=legacy
        ).unwrap()

        class FakeSnapshot:
            _semantic = snapshot._semantic
            snapshot_digest = snapshot.snapshot_digest

            def _require_valid(self) -> None:
                return None

        assert (
            _code(
                create_adoption_token(
                    FakeSnapshot(),
                    identity,
                    source,
                    legacy=legacy,
                )
            )
            == "config.adoption_stale"
        )
        assert (
            _code(
                create_adoption_token(
                    snapshot,
                    identity,
                    source,
                    legacy=legacy,
                    runner_version="0.1.0",
                )
            )
            == "config.adoption_stale"
        )
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=legacy,
                    adoption_token=token,
                    policy_version="2.0",
                )
            )
            == "config.adoption_stale"
        )
        adopted = plan_config(
            snapshot,
            identity,
            source,
            receipt=None,
            legacy=legacy,
            adoption_token=token,
        ).unwrap()
        assert adopted.operation.value == "adopt_recognized_legacy"


def test_exact_legacy_can_use_explicit_alternate_without_adoption(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import (
        CollisionPolicy,
        ConfigClassification,
        ConfigOperation,
        plan_config,
    )
    from zagrosi_forge.install.contracts import install_identity_digest
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    checkout = tmp_path / "checkout/zagrosi-forge"
    checkout.mkdir(parents=True)
    raw = _fixture("legacy/exact.toml", CHECKOUT=os.fspath(checkout))
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        _source,
        _proof,
        _home,
        root,
        authority,
    ):
        alternate = "zagrosi-local-" + install_identity_digest(identity)[:24]
        alternate_source = authority.plan_owned_path(
            root,
            _reference(_source_relative(identity, alternate)),
            expected_depth=5,
        ).unwrap()
        try:
            plan = plan_config(
                snapshot,
                identity,
                alternate_source,
                receipt=None,
                legacy=load_legacy_install_catalog().unwrap(),
                collision_policy=CollisionPolicy.ALTERNATE,
            ).unwrap()
            assert plan.classification is ConfigClassification.RECOGNIZED_LEGACY
            assert plan.operation is ConfigOperation.COLLISION_ALTERNATIVE
            assert plan.effective_marketplace_id == alternate
        finally:
            alternate_source.close()


def test_adoption_token_rejects_source_authority_from_a_different_home(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import create_adoption_token
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    checkout = tmp_path / "checkout/zagrosi-forge"
    checkout.mkdir(parents=True)
    raw = _fixture("legacy/exact.toml", CHECKOUT=os.fspath(checkout))
    first_base = tmp_path / "first"
    second_base = tmp_path / "second"
    first_base.mkdir()
    second_base.mkdir()
    with _config_context(first_base, raw) as (
        snapshot,
        identity,
        _first_source,
        *_first_rest,
    ):
        with _config_context(second_base, None, identity=identity) as (
            _second_snapshot,
            _second_identity,
            second_source,
            *_second_rest,
        ):
            assert (
                _code(
                    create_adoption_token(
                        snapshot,
                        identity,
                        second_source,
                        legacy=load_legacy_install_catalog().unwrap(),
                    )
                )
                == "config.adoption_stale"
            )


@pytest.mark.parametrize(
    "fixture",
    [
        "legacy/near-source-type.toml",
        "legacy/near-source.toml",
        "legacy/near-enabled.toml",
        "legacy/near-id.toml",
        "legacy/near-extra.toml",
        "legacy/near-wrong-type.toml",
    ],
)
def test_legacy_near_miss_is_unmanaged_collision(tmp_path: Path, fixture: str) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    checkout = tmp_path / "checkout/zagrosi-forge"
    checkout.mkdir(parents=True)
    raw = _fixture(fixture, CHECKOUT=os.fspath(checkout))
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                )
            )
            == "config.owner_collision"
        )


def test_duplicate_legacy_table_is_parse_failed_not_adoptable(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import snapshot_config
    from zagrosi_forge.install.paths import PlatformPathAuthority

    checkout = tmp_path / "checkout/zagrosi-forge"
    checkout.mkdir(parents=True)
    raw = _fixture(
        "legacy/near-duplicate-table.toml",
        CHECKOUT=os.fspath(checkout),
    )
    home = tmp_path / "codex-home"
    _private_directory(home)
    _write_private_test_file(home, "config.toml", raw)
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        assert _code(snapshot_config(proof)) == "config.parse_failed"
    finally:
        proof.close()
        root.close()


def test_name_or_source_match_without_receipt_is_not_managed(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    identity = _identity()
    raw = b'[marketplaces.zagrosi]\nsource_type = "local"\nsource = "/tmp/generated"\n'
    with _config_context(tmp_path, raw, identity=identity) as (
        snapshot,
        candidate,
        source,
        *_rest,
    ):
        assert (
            _code(
                plan_config(
                    snapshot,
                    candidate,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                )
            )
            == "config.owner_collision"
        )


def test_default_collision_rejects_without_prompt(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    raw = b'[marketplaces.zagrosi]\nsource_type = "git"\nsource = "redacted"\n'
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                )
            )
            == "config.owner_collision"
        )


def test_explicit_alternate_id_is_fixed_and_candidate_deterministic(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import CollisionPolicy, plan_config
    from zagrosi_forge.install.contracts import install_identity_digest
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    raw = b'[marketplaces.zagrosi]\nsource_type = "git"\nsource = "safe"\n'
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        _source,
        _proof,
        _home,
        root,
        authority,
    ):
        alternate = "zagrosi-local-" + install_identity_digest(identity)[:24]
        source = authority.plan_owned_path(
            root,
            _reference(_source_relative(identity, alternate)),
            expected_depth=5,
        ).unwrap()
        try:
            first = plan_config(
                snapshot,
                identity,
                source,
                receipt=None,
                legacy=load_legacy_install_catalog().unwrap(),
                collision_policy=CollisionPolicy.ALTERNATE,
            ).unwrap()
            second = plan_config(
                snapshot,
                identity,
                source,
                receipt=None,
                legacy=load_legacy_install_catalog().unwrap(),
                collision_policy=CollisionPolicy.ALTERNATE,
            ).unwrap()
            assert first == second
            assert first.effective_marketplace_id == alternate
        finally:
            source.close()


def test_alternate_plan_rejects_canonical_source_generation(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import CollisionPolicy, plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    raw = b'[marketplaces.zagrosi]\nsource_type = "git"\nsource = "safe"\n'
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        result = plan_config(
            snapshot,
            identity,
            source,
            receipt=None,
            legacy=load_legacy_install_catalog().unwrap(),
            collision_policy=CollisionPolicy.ALTERNATE,
        )
        assert _code(result) == "config.owner_collision"


def test_occupied_alternate_id_different_identity_stops(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import CollisionPolicy, plan_config
    from zagrosi_forge.install.contracts import install_identity_digest
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    identity = _identity()
    alternate = "zagrosi-local-" + install_identity_digest(identity)[:24]
    raw = (
        f'[marketplaces.zagrosi]\nsource_type = "git"\nsource = "safe"\n'
        f'[marketplaces.{alternate}]\nsource_type = "local"\nsource = "occupied"\n'
    ).encode()
    with _config_context(tmp_path, raw, identity=identity) as (
        snapshot,
        candidate,
        source,
        *_rest,
    ):
        assert (
            _code(
                plan_config(
                    snapshot,
                    candidate,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                    collision_policy=CollisionPolicy.ALTERNATE,
                )
            )
            == "config.alternate_collision"
        )


def test_adoption_and_alternate_are_mutually_exclusive(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import CollisionPolicy, plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    checkout = tmp_path / "checkout/zagrosi-forge"
    checkout.mkdir(parents=True)
    raw = _fixture("legacy/exact.toml", CHECKOUT=os.fspath(checkout))
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                    collision_policy=CollisionPolicy.ALTERNATE,
                    adoption_token="0" * 64,
                )
            )
            == "config.adoption_stale"
        )


def test_adoption_token_is_rejected_outside_exact_legacy(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    with _config_context(tmp_path, None) as (snapshot, identity, source, *_rest):
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                    adoption_token="0" * 64,
                )
            )
            == "config.adoption_stale"
        )


def test_snapshot_mutated_toml_document_cannot_change_rendered_candidate(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    with _config_context(tmp_path, b'user = "safe"\n') as (
        snapshot,
        identity,
        source,
        *_rest,
    ):
        snapshot._document["secret_token"] = "CANARY-MUTATION"
        result = plan_config(
            snapshot,
            identity,
            source,
            receipt=None,
            legacy=load_legacy_install_catalog().unwrap(),
        )
        assert _code(result) == "config.external_change"
        assert "CANARY-MUTATION" not in repr(result)


def test_mutated_metadata_or_limit_policy_cannot_widen_snapshot_authority(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import (
        ConfigMetadataPolicy,
        plan_config,
        snapshot_config,
    )
    from zagrosi_forge.install.ownership import load_legacy_install_catalog
    from zagrosi_forge.install.policies import LIMIT_POLICY, LimitPolicy

    with _config_context(tmp_path, b"safe = true\n") as (
        _snapshot,
        identity,
        source,
        proof,
        *_rest,
    ):
        metadata = ConfigMetadataPolicy()
        object.__setattr__(metadata, "posix_modes", (0o600, 0o644, 0o666))
        assert _code(snapshot_config(proof, metadata_policy=metadata)) == (
            "config.unsupported_metadata"
        )

        widened = dict(LIMIT_POLICY.values)
        widened["toml_bytes"] += 1
        limits = LimitPolicy(version="1.0", values=widened)
        assert _code(snapshot_config(proof, limits=limits)) == "config.limit_exceeded"

        narrowed = dict(LIMIT_POLICY.values)
        narrowed["toml_nodes"] = 8
        narrow_limits = LimitPolicy(version="1.0", values=narrowed)
        narrow_snapshot = snapshot_config(proof, limits=narrow_limits).unwrap()
        assert (
            _code(
                plan_config(
                    narrow_snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                )
            )
            == "config.limit_exceeded"
        )


def test_structural_subclasses_cannot_mint_config_authority(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import plan_config, snapshot_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog
    from zagrosi_forge.install.paths import ConfigPathProof, PlannedOwnedPath

    class FakeConfigPathProof(ConfigPathProof):
        parent_identity = (1, 2)
        leaf_identity = None

        def __init__(self) -> None:
            pass

        def _require_current(self) -> None:
            return None

        def open_leaf(self) -> None:
            return None

    assert _code(snapshot_config(FakeConfigPathProof())) == "config.external_change"

    with _config_context(tmp_path, None) as (
        snapshot,
        identity,
        source,
        *_rest,
    ):
        relative = _reference(_source_relative(identity))

        class FakePlannedOwnedPath(PlannedOwnedPath):
            def __init__(self) -> None:
                pass

            @property
            def relative(self):
                return relative

            def _require_current(self) -> None:
                return None

            def _config_source_value(self) -> str:
                return os.fspath(Path("/outside/plugins") / Path(relative.value))

        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    FakePlannedOwnedPath(),
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                )
            )
            == "config.owner_collision"
        )
        object.__setattr__(source, "_native_source", "/outside/attacker-controlled")
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                )
            )
            == "config.owner_collision"
        )


def test_config_capabilities_reject_generic_dataclass_export(tmp_path: Path) -> None:
    from dataclasses import asdict

    with _config_context(tmp_path, _fixture("cases/preserve.toml")) as (
        snapshot,
        identity,
        source,
        *_rest,
    ):
        plan, candidate = _render_in_context(snapshot, identity, source)
        for capability in (snapshot, plan, candidate):
            with pytest.raises(TypeError):
                asdict(capability)


@pytest.mark.parametrize(
    ("marketplace_id", "plugin_id"),
    (("other-marketplace", "zagrosi-forge"), ("zagrosi", "other-plugin")),
)
def test_candidate_identity_namespace_must_be_canonical(
    tmp_path: Path,
    marketplace_id: str,
    plugin_id: str,
) -> None:
    from dataclasses import replace

    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    with _config_context(tmp_path, None) as (
        snapshot,
        identity,
        _source,
        _proof,
        _home,
        root,
        authority,
    ):
        candidate = replace(
            identity,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
        )
        relative = (
            f"sources/zagrosi/{plugin_id}/{candidate.install_version}/marketplace"
        )
        source = authority.plan_owned_path(
            root,
            _reference(relative),
            expected_depth=5,
        ).unwrap()
        try:
            assert (
                _code(
                    plan_config(
                        snapshot,
                        candidate,
                        source,
                        receipt=None,
                        legacy=load_legacy_install_catalog().unwrap(),
                    )
                )
                == "config.owner_collision"
            )
        finally:
            source.close()


def test_mutated_identity_and_legacy_catalog_cannot_mint_plan(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    with _config_context(tmp_path, None) as (snapshot, identity, source, *_rest):
        legacy = load_legacy_install_catalog().unwrap()
        object.__setattr__(identity, "policy_digest", "not-a-digest")
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=legacy,
                )
            )
            == "config.owner_collision"
        )

    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    with _config_context(catalog_root, None) as (
        snapshot,
        identity,
        source,
        *_rest,
    ):
        legacy = load_legacy_install_catalog().unwrap()
        object.__setattr__(legacy, "catalog_digest", "0" * 64)
        result = plan_config(
            snapshot,
            identity,
            source,
            receipt=None,
            legacy=legacy,
        )
        assert _code(result) == "config.owner_collision"
        assert result.error is not None
        assert result.error.exit_category == 13


def test_stale_adoption_token_fails_after_snapshot_change(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import (
        create_adoption_token,
        plan_config,
        snapshot_config,
    )
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    checkout = tmp_path / "checkout/zagrosi-forge"
    checkout.mkdir(parents=True)
    raw = _fixture("legacy/exact.toml", CHECKOUT=os.fspath(checkout))
    legacy = load_legacy_install_catalog().unwrap()
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        _proof,
        home,
        root,
        authority,
    ):
        token = create_adoption_token(
            snapshot, identity, source, legacy=legacy
        ).unwrap()
        config = home / "config.toml"
        config.write_bytes(raw + b"\n# external\n")
        if os.name != "nt":
            config.chmod(0o600)
        fresh_proof = authority.prove_config_path(root).unwrap()
        try:
            fresh_snapshot = snapshot_config(fresh_proof).unwrap()
            assert (
                _code(
                    plan_config(
                        fresh_snapshot,
                        identity,
                        source,
                        receipt=None,
                        legacy=legacy,
                        adoption_token=token,
                    )
                )
                == "config.adoption_stale"
            )
        finally:
            fresh_proof.close()


def test_stale_adoption_token_fails_after_source_root_rebind(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import create_adoption_token, plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog
    from zagrosi_forge.install.paths import PlatformPathAuthority

    checkout = tmp_path / "checkout/zagrosi-forge"
    checkout.mkdir(parents=True)
    raw = _fixture("legacy/exact.toml", CHECKOUT=os.fspath(checkout))
    legacy = load_legacy_install_catalog().unwrap()
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        _authority,
    ):
        token = create_adoption_token(
            snapshot,
            identity,
            source,
            legacy=legacy,
        ).unwrap()
        source.close()
        proof.close()
        root.close()
        plugins = home / "plugins"
        plugins.rename(home / "displaced-plugins")
        authority = PlatformPathAuthority()
        fresh_root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
        fresh_source = authority.plan_owned_path(
            fresh_root,
            _reference(_source_relative(identity)),
            expected_depth=5,
        ).unwrap()
        try:
            assert (
                _code(
                    plan_config(
                        snapshot,
                        identity,
                        fresh_source,
                        receipt=None,
                        legacy=legacy,
                        adoption_token=token,
                    )
                )
                == "config.adoption_stale"
            )
        finally:
            fresh_source.close()
            fresh_root.close()


def test_config_plan_is_identical_across_dry_update_and_install(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    with _config_context(tmp_path, None) as (snapshot, identity, source, *_rest):
        kwargs = {
            "receipt": None,
            "legacy": load_legacy_install_catalog().unwrap(),
        }
        assert (
            plan_config(snapshot, identity, source, **kwargs).unwrap()
            == plan_config(snapshot, identity, source, **kwargs).unwrap()
        )


def test_config_plan_rejects_source_authority_from_a_different_home(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    first_base = tmp_path / "first"
    second_base = tmp_path / "second"
    first_base.mkdir()
    second_base.mkdir()
    with _config_context(first_base, None) as (
        snapshot,
        identity,
        _first_source,
        *_first_rest,
    ):
        with _config_context(second_base, None, identity=identity) as (
            _second_snapshot,
            _second_identity,
            second_source,
            *_second_rest,
        ):
            assert (
                _code(
                    plan_config(
                        snapshot,
                        identity,
                        second_source,
                        receipt=None,
                        legacy=load_legacy_install_catalog().unwrap(),
                    )
                )
                == "config.owner_collision"
            )


def test_recursive_snapshot_mutation_returns_bounded_config_error(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    with _config_context(tmp_path, None) as (snapshot, identity, source, *_rest):
        recursive: dict[str, object] = {}
        recursive["self"] = recursive
        object.__setattr__(snapshot, "_semantic", recursive)
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                )
            )
            == "config.external_change"
        )


def _render_in_context(snapshot: Any, identity: Any, source: Any):
    from zagrosi_forge.install.config import plan_config, render_config_candidate
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    plan = plan_config(
        snapshot,
        identity,
        source,
        receipt=None,
        legacy=load_legacy_install_catalog().unwrap(),
    ).unwrap()
    return plan, render_config_candidate(snapshot, plan).unwrap()


def test_recursive_plan_and_candidate_mutation_return_bounded_errors(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import (
        _candidate_bytes,
        render_config_candidate,
    )
    from zagrosi_forge.install.contracts import ForgeError

    with _config_context(tmp_path, None) as (snapshot, identity, source, *_rest):
        plan, _candidate = _render_in_context(snapshot, identity, source)
        recursive: list[object] = []
        recursive.append(recursive)
        object.__setattr__(plan, "findings", recursive)
        assert _code(render_config_candidate(snapshot, plan)) == "config.adoption_stale"

        _fresh_plan, candidate = _render_in_context(snapshot, identity, source)
        object.__setattr__(candidate, "preview", recursive)
        with pytest.raises(ForgeError) as raised:
            _candidate_bytes(candidate)
        assert raised.value.code == "config.external_change"
        assert raised.value.exit_category == 13


def test_candidate_edit_reparses_with_tomlkit_and_tomllib(tmp_path: Path) -> None:
    import tomllib

    from zagrosi_forge._vendor import tomlkit
    from zagrosi_forge.install.config import _candidate_bytes

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        _plan, candidate = _render_in_context(snapshot, identity, source)
        rendered = _candidate_bytes(candidate)
        assert tomllib.loads(rendered.decode()) == tomlkit.loads(rendered.decode())


@pytest.mark.parametrize(
    "fixture",
    tuple(
        fixture
        for fixture, declaration in PROTECTED_FIXTURES.items()
        if declaration.get("scenario", "add") == "add"
    ),
)
def test_declared_fixture_unmanaged_tree_and_protected_spans_are_unchanged(
    tmp_path: Path,
    fixture: str,
) -> None:
    import tomllib

    from zagrosi_forge._vendor import tomlkit
    from zagrosi_forge.install.config import (
        _candidate_bytes,
        _without_managed,
        render_config_candidate,
    )

    declaration = PROTECTED_FIXTURES[fixture]
    raw = _fixture(fixture)
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        plan, candidate = _render_in_context(snapshot, identity, source)
        rendered = _candidate_bytes(candidate)
        reparsed = tomllib.loads(rendered.decode())
        assert reparsed == tomlkit.loads(rendered.decode())
        assert (
            _without_managed(reparsed, "zagrosi") == declaration["expected_unmanaged"]
        )
        for span in declaration["protected_spans"]:
            encoded = span.encode()
            assert raw.count(encoded) == rendered.count(encoded) == 1
        repeated = render_config_candidate(snapshot, plan).unwrap()
        assert _candidate_bytes(repeated) == rendered
        assert repeated.byte_digest == candidate.byte_digest
        assert candidate.unmanaged_semantic_digest == snapshot.semantic_digest


def test_protected_unmanaged_comment_adjacency_is_unchanged(tmp_path: Path) -> None:
    import tomllib

    from zagrosi_forge.install.config import _without_managed, plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    raw = _fixture("cases/protected-adjacency.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        _proof,
        home,
        *_rest,
    ):
        assert (
            _code(
                plan_config(
                    snapshot,
                    identity,
                    source,
                    receipt=None,
                    legacy=load_legacy_install_catalog().unwrap(),
                )
            )
            == "config.representation_unsupported"
        )
        declaration = PROTECTED_FIXTURES["cases/protected-adjacency.toml"]
        assert (
            _without_managed(tomllib.loads(raw.decode()), "zagrosi")
            == declaration["expected_unmanaged"]
        )
        assert (home / "config.toml").read_bytes() == raw
        for span in declaration["protected_spans"]:
            assert raw.count(span.encode()) == 1


def test_config_edit_is_byte_idempotent(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import _candidate_bytes, render_config_candidate

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        plan, first = _render_in_context(snapshot, identity, source)
        second = render_config_candidate(snapshot, plan).unwrap()
        assert _candidate_bytes(first) == _candidate_bytes(second)
        assert first.byte_digest == second.byte_digest


def test_comments_crlf_and_no_final_newline_are_preserved_outside_managed_span(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import _candidate_bytes

    raw = b'# protected comment\r\ntheme = "dark"\r\nsecret_token = "CANARY-CRLF"'
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        _plan, candidate = _render_in_context(snapshot, identity, source)
        rendered = _candidate_bytes(candidate)
        assert b'# protected comment\r\ntheme = "dark"\r\n' in rendered
        assert b'secret_token = "CANARY-CRLF"' in rendered
        assert rendered.count(b"CANARY-CRLF") == 1
        assert b"\n" not in rendered.replace(b"\r\n", b"")
        assert not rendered.endswith((b"\r", b"\n"))


def test_inline_dotted_quoted_and_array_tables_preserve_user_data(
    tmp_path: Path,
) -> None:
    import tomllib

    from zagrosi_forge.install.config import _candidate_bytes

    raw = _fixture("cases/complex-representation.toml")
    before = tomllib.loads(raw.decode())
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        _plan, candidate = _render_in_context(snapshot, identity, source)
        after = tomllib.loads(_candidate_bytes(candidate).decode())
        assert after["profile"] == before["profile"]
        assert after["tool"] == before["tool"]
        assert after["servers"] == before["servers"]


def test_unicode_and_unusual_whitespace_preserve_user_data(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import _candidate_bytes

    raw = _fixture("cases/unicode-whitespace.toml")
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        _plan, candidate = _render_in_context(snapshot, identity, source)
        rendered = _candidate_bytes(candidate)
        for token in ("دجلة", "雪", "spaced-key", "unusual spacing"):
            assert token.encode() in rendered


def test_config_preview_is_string_structural_and_redacted(tmp_path: Path) -> None:
    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        _proof,
        home,
        _root,
        _authority,
    ):
        plan, _candidate = _render_in_context(snapshot, identity, source)
        assert isinstance(plan.preview, str)
        assert plan.reason == "config.plan.add"
        assert plan.findings == ()
        assert "redacted managed config" in plan.preview
        assert "CANARY" not in plan.preview
        assert os.fspath(home) not in plan.preview


def test_unpreservable_representation_returns_manual_unsupported(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import plan_config
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    with _config_context(tmp_path, b"marketplaces = []\n") as (
        snapshot,
        identity,
        source,
        _proof,
        home,
        *_rest,
    ):
        result = plan_config(
            snapshot,
            identity,
            source,
            receipt=None,
            legacy=load_legacy_install_catalog().unwrap(),
        )
        assert _code(result) == "config.representation_unsupported"
        assert result.error is not None
        recovery = " ".join(result.error.recovery_instructions)
        assert "redacted manual config" in recovery
        assert "preserve all other nodes" in recovery
        assert "CANARY" not in repr(result)
        assert os.fspath(home) not in repr(result)
        assert any(
            finding.code == "config.representation_unsupported"
            and finding.subject == "config.managed_projection"
            for finding in result.findings
        )


def test_parse_error_and_limit_failure_have_bounded_safe_findings(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import snapshot_config
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.policies import LIMIT_POLICY

    nested = b"value = " + (b"{ child = " * 80) + b"1" + (b" }" * 80)
    nodes = b"".join(f"node_{index} = {index}\n".encode() for index in range(4097))
    for index, (raw, expected) in enumerate(
        (
            (b"broken = [\nCANARY-PARSE", "config.parse_failed"),
            (b"x" * (LIMIT_POLICY.value("toml_bytes") + 1), "config.limit_exceeded"),
            (nested, "config.limit_exceeded"),
            (nodes, "config.limit_exceeded"),
        )
    ):
        home = tmp_path / f"home-{index}"
        _private_directory(home)
        _write_private_test_file(home, "config.toml", raw)
        authority = PlatformPathAuthority()
        root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
        proof = authority.prove_config_path(root).unwrap()
        try:
            result = snapshot_config(proof)
            assert _code(result) == expected
            assert "CANARY" not in repr(result)
            assert len(result.error.safe_message) < 160  # type: ignore[union-attr]
        finally:
            proof.close()
            root.close()


def test_large_valid_config_uses_toml_policy_not_record_encoder_limit(
    tmp_path: Path,
) -> None:
    payload = b"a" * (300 * 1024)
    raw = b'value = "' + payload + b'"\n'
    with _config_context(tmp_path, raw) as (snapshot, *_rest):
        assert snapshot.present
        assert snapshot.byte_digest == hashlib.sha256(raw).hexdigest()


def test_legal_nonfinite_toml_floats_round_trip_semantically(tmp_path: Path) -> None:
    raw = b"positive = inf\nnegative = -inf\nnot_a_number = nan\n"
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        _plan, candidate = _render_in_context(snapshot, identity, source)
        rendered = candidate._raw
        assert b"positive = inf" in rendered
        assert b"negative = -inf" in rendered
        assert b"not_a_number = nan" in rendered


def test_mutated_plan_returns_bounded_category_13_failure(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import render_config_candidate

    with _config_context(tmp_path, None) as (snapshot, identity, source, *_rest):
        plan, _candidate = _render_in_context(snapshot, identity, source)
        object.__setattr__(plan, "classification", "mutated")
        result = render_config_candidate(snapshot, plan)
        assert _code(result) == "config.adoption_stale"
        assert result.error is not None
        assert result.error.exit_category == 13


def test_secret_config_canary_never_reaches_result_or_snapshot(tmp_path: Path) -> None:
    from zagrosi_forge.install.config import _candidate_bytes

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (snapshot, identity, source, *_rest):
        plan, candidate = _render_in_context(snapshot, identity, source)
        assert "CANARY-DO-NOT-LEAK" not in repr(snapshot)
        assert "CANARY-DO-NOT-LEAK" not in repr(plan)
        assert "CANARY-DO-NOT-LEAK" not in repr(candidate)
        assert b"CANARY-DO-NOT-LEAK" in _candidate_bytes(candidate)


def _atomic_inputs(
    snapshot: Any,
    identity: Any,
    source: Any,
    *,
    persistent_backup: bool = False,
):
    from zagrosi_forge.install.config import plan_config, render_config_candidate
    from zagrosi_forge.install.ownership import load_legacy_install_catalog

    plan = plan_config(
        snapshot,
        identity,
        source,
        receipt=None,
        legacy=load_legacy_install_catalog().unwrap(),
        persistent_backup=persistent_backup,
    ).unwrap()
    return render_config_candidate(snapshot, plan).unwrap()


def test_same_byte_external_replacement_invalidates_file_identity(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        config = home / "config.toml"
        config.rename(home / "displaced.toml")
        config.write_bytes(raw)
        if os.name != "nt":
            config.chmod(0o600)
        result = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-same-byte"),
        )
        assert _code(result) == "config.external_change"


def test_parent_swap_or_security_metadata_change_aborts(tmp_path: Path) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        if os.name == "nt":
            proof._namespace.close()
        else:
            (home / "config.toml").chmod(0o644)
        result = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-metadata-change"),
        )
        assert _code(result) == "config.external_change"
        assert not any("zagrosi-config" in item.name for item in home.iterdir())


def test_symlink_or_reparse_config_is_rejected(tmp_path: Path) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority

    home = tmp_path / "codex-home"
    _private_directory(home)
    if os.name == "nt":
        external = tmp_path / "external-config"
        _private_directory(external)
        completed = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "mklink",
                "/J",
                os.fspath(home / "config.toml"),
                os.fspath(external),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            pytest.fail(
                "required Windows junction fixture could not be created: "
                + completed.stderr.strip()
            )
    else:
        external = tmp_path / "external.toml"
        external.write_bytes(b"safe = true\n")
        (home / "config.toml").symlink_to(external)
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    try:
        result = authority.prove_config_path(root)
        assert result.error is not None
        assert result.error.code in {
            "path.linked_leaf",
            "path.outside_root",
            "path.reparse",
        }
    finally:
        root.close()


def test_config_created_after_absence_snapshot_aborts(tmp_path: Path) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )

    with _config_context(tmp_path, None) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        config = home / "config.toml"
        config.write_bytes(b"third_party = true\n")
        if os.name != "nt":
            config.chmod(0o600)
        result = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-absence-race"),
        )
        assert _code(result) == "config.external_change"
        assert config.read_bytes() == b"third_party = true\n"


def test_postreplace_classifier_requires_exact_sealed_evidence(tmp_path: Path) -> None:
    from zagrosi_forge.install.atomic_file import (
        ConfigCommitState,
        begin_config_transaction,
        classify_config_after_replace,
        cleanup_config_recovery,
        prepare_atomic_candidate,
        promote_config_backup,
    )
    from zagrosi_forge.install.config import _candidate_bytes

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-exact-classifier"),
        ).unwrap()
        committed = _commit_acknowledged(prepared, expected=snapshot).unwrap()
        assert (
            classify_config_after_replace(committed).unwrap()
            is ConfigCommitState.CANDIDATE
        )
        config = home / "config.toml"
        candidate_raw = _candidate_bytes(candidate)
        mutated = bytearray(candidate_raw)
        mutated[-1] = ord(" ") if mutated[-1] != ord(" ") else ord("\n")
        config.write_bytes(mutated)
        assert (
            classify_config_after_replace(committed).unwrap()
            is ConfigCommitState.THIRD_PARTY
        )
        assert _code(cleanup_config_recovery(committed)) == "config.external_change"
        config.write_bytes(candidate_raw)
        assert (
            classify_config_after_replace(committed).unwrap()
            is ConfigCommitState.CANDIDATE
        )
        promote_config_backup(committed).unwrap()
        cleanup_config_recovery(committed).unwrap()


def test_real_atomic_commit_yields_distinct_journal_observation(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.atomic_file import (
        ConfigCommitState,
        begin_config_transaction,
        cleanup_config_recovery,
        prepare_atomic_candidate,
    )
    from zagrosi_forge.install.config import snapshot_config
    from zagrosi_forge.install.journal import (
        JournalConfigIdentity,
        _config_result_matches,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        before,
        identity,
        source,
        proof,
        _home,
        root,
        authority,
    ):
        candidate = _atomic_inputs(before, identity, source)
        atomic = prepare_atomic_candidate(
            proof,
            before,
            candidate,
            begin_config_transaction("tx-journal-result"),
        ).unwrap()
        committed = _commit_acknowledged(atomic, expected=before).unwrap()
        try:
            assert committed.state is ConfigCommitState.CANDIDATE
            fresh = authority.prove_config_path(root).unwrap()
            try:
                after = snapshot_config(fresh).unwrap()
            finally:
                fresh.close()

            descriptor = committed.recovery_descriptor
            planned = JournalConfigIdentity(
                parent_identity=before.parent_identity,
                leaf_identity=None,
                byte_digest=candidate.byte_digest,
                semantic_digest=candidate.semantic_digest,
                metadata_fingerprint=candidate.metadata_fingerprint,
                snapshot_digest=candidate.snapshot_digest,
                target_metadata_digest=descriptor.target_metadata_digest,
            )
            observed = JournalConfigIdentity(
                parent_identity=after.parent_identity,
                leaf_identity=after.leaf_identity,
                byte_digest=after.byte_digest,
                semantic_digest=after.semantic_digest,
                metadata_fingerprint=after.metadata_fingerprint,
                snapshot_digest=after.snapshot_digest,
                target_metadata_digest=descriptor.target_metadata_digest,
            )

            assert committed.installed_identity == committed.candidate_identity
            assert committed.installed_identity == after.leaf_identity
            assert before.leaf_identity != after.leaf_identity
            assert before.metadata_fingerprint != after.metadata_fingerprint
            assert before.snapshot_digest != after.snapshot_digest
            assert candidate.snapshot_digest == before.snapshot_digest
            assert _config_result_matches(
                planned,
                descriptor.to_record(),
                observed,
            )
        finally:
            cleanup_config_recovery(committed).unwrap()


def test_postreplace_classifier_rejects_unsealed_evidence(tmp_path: Path) -> None:
    from zagrosi_forge.install.atomic_file import classify_config_after_replace

    with _config_context(tmp_path, None) as (
        *_prefix,
        proof,
        _home,
        _root,
        _authority,
    ):
        assert _code(classify_config_after_replace(proof)) == "config.external_change"


def test_atomic_candidate_uses_same_directory_restrictive_exclusive_file(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.atomic_file import (
        ConfigCommitState,
        begin_config_transaction,
        cleanup_config_recovery,
        prepare_atomic_candidate,
        promote_config_backup,
    )

    with _config_context(tmp_path, None) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-success"),
        ).unwrap()
        with prepared:
            candidate_path = home / prepared.candidate_reference
            snapshot_path = home / prepared.snapshot_reference
            assert candidate_path.is_file()
            assert snapshot_path.is_file()
            if os.name != "nt":
                assert stat.S_IMODE(candidate_path.stat().st_mode) == 0o600
                assert stat.S_IMODE(snapshot_path.stat().st_mode) == 0o600
            committed = _commit_acknowledged(prepared, expected=snapshot).unwrap()
            assert committed.state is ConfigCommitState.CANDIDATE
            assert committed.durability_confirmed
            descriptor = committed.recovery_descriptor
            record = descriptor.to_record()
            assert record["candidate_byte_digest"] == candidate.byte_digest
            assert os.fspath(home) not in repr(record)
            assert promote_config_backup(committed).unwrap() is None
            cleanup_config_recovery(committed).unwrap()
            assert not snapshot_path.exists()
        assert (home / "config.toml").is_file()


def test_config_recovery_descriptor_roundtrips_and_reopens_after_restart(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-reopen-success"),
        ).unwrap()
        atomic_file.acknowledge_config_preparation(prepared).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        record = json.loads(json.dumps(committed.recovery_descriptor.to_record()))
        prepared.close()

        decoded = atomic_file.decode_config_recovery_descriptor(record).unwrap()
        fresh_proof = authority.prove_config_path(root).unwrap()
        try:
            reopened = atomic_file.reopen_config_recovery(
                fresh_proof,
                decoded,
            ).unwrap()
            assert (
                atomic_file.classify_reopened_config_recovery(reopened).unwrap()
                is atomic_file.ConfigCommitState.CANDIDATE
            )
            assert (
                _code(atomic_file.cleanup_reopened_config_recovery(decoded))
                == "config.external_change"
            )
            atomic_file.cleanup_reopened_config_recovery(reopened).unwrap()
        finally:
            fresh_proof.close()
        assert (home / "config.toml").is_file()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="POSIX nonblocking FIFO contract")
def test_reopened_classifier_rejects_nonregular_config_without_blocking(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-reopen-fifo"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        descriptor = committed.recovery_descriptor
        prepared.close()
        fresh_proof = authority.prove_config_path(root).unwrap()
        reopened = atomic_file.reopen_config_recovery(
            fresh_proof,
            atomic_file.decode_config_recovery_descriptor(
                descriptor.to_record()
            ).unwrap(),
        ).unwrap()
        config = home / "config.toml"
        retained = home / "candidate-retained.toml"
        config.rename(retained)
        os.mkfifo(config, 0o600)
        child = os.fork()
        waited = False
        try:
            if child == 0:
                try:
                    observed = atomic_file.classify_reopened_config_recovery(reopened)
                    accepted = observed.is_ok and (
                        observed.unwrap() is atomic_file.ConfigCommitState.THIRD_PARTY
                    )
                    os._exit(0 if accepted else 1)
                except BaseException:
                    os._exit(2)
            deadline = time.monotonic() + 2.0
            status = 0
            while time.monotonic() < deadline:
                process, status = os.waitpid(child, os.WNOHANG)
                if process == child:
                    waited = True
                    break
                time.sleep(0.01)
            if not waited:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
                waited = True
                pytest.fail("reopened config classification blocked on a FIFO")
            assert os.waitstatus_to_exitcode(status) == 0
        finally:
            if child != 0 and not waited:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
            config.unlink()
            retained.rename(config)
        atomic_file.cleanup_reopened_config_recovery(reopened).unwrap()
        fresh_proof.close()


def test_reopened_classifier_revalidates_current_config_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-reopen-current-race"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        descriptor = committed.recovery_descriptor
        prepared.close()
        fresh_proof = authority.prove_config_path(root).unwrap()
        reopened = atomic_file.reopen_config_recovery(
            fresh_proof,
            atomic_file.decode_config_recovery_descriptor(
                descriptor.to_record()
            ).unwrap(),
        ).unwrap()
        original_read = atomic_file._recovery_descriptor_bytes
        retained = home / "candidate-retained.toml"
        swapped = False

        def swap_current_after_open(opened: int) -> bytes:
            nonlocal swapped
            observed = original_read(opened)
            if not swapped:
                (home / "config.toml").rename(retained)
                _write_private_test_file(
                    home,
                    "config.toml",
                    b"third_party = true\n",
                )
                swapped = True
            return observed

        monkeypatch.setattr(
            atomic_file,
            "_recovery_descriptor_bytes",
            swap_current_after_open,
        )
        assert (
            atomic_file.classify_reopened_config_recovery(reopened).unwrap()
            is atomic_file.ConfigCommitState.THIRD_PARTY
        )
        assert (
            _code(atomic_file.cleanup_reopened_config_recovery(reopened))
            == "config.commit_ambiguous"
        )
        assert (home / descriptor.snapshot_reference).is_file()
        (home / "config.toml").unlink()
        retained.rename(home / "config.toml")
        atomic_file.cleanup_reopened_config_recovery(reopened).unwrap()
        fresh_proof.close()


def test_reopened_classifier_revalidates_observed_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    with _config_context(tmp_path, None) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-reopen-absence-race"),
        ).unwrap()
        descriptor = prepared.recovery_descriptor
        prepared._retain()
        prepared.close()
        fresh_proof = authority.prove_config_path(root).unwrap()
        reopened = atomic_file.reopen_config_recovery(
            fresh_proof,
            atomic_file.decode_config_recovery_descriptor(
                descriptor.to_record()
            ).unwrap(),
        ).unwrap()
        original_open = atomic_file._open_recovery_name
        injected = False

        def create_after_absence(
            parent: int,
            name: str,
            *,
            delete: bool = False,
        ) -> int:
            nonlocal injected
            opened = original_open(parent, name, delete=delete)
            if (
                name == atomic_file._CONFIG_NAME
                and not injected
                and not atomic_file._descriptor_is_open(opened)
            ):
                _write_private_test_file(
                    home,
                    "config.toml",
                    b"third_party = true\n",
                )
                injected = True
            return opened

        monkeypatch.setattr(atomic_file, "_open_recovery_name", create_after_absence)
        assert (
            atomic_file.classify_reopened_config_recovery(reopened).unwrap()
            is atomic_file.ConfigCommitState.THIRD_PARTY
        )
        assert (
            _code(atomic_file.rollback_reopened_config_recovery(reopened))
            == "config.commit_ambiguous"
        )
        assert (home / descriptor.candidate_reference).is_file()
        (home / "config.toml").unlink()
        atomic_file.rollback_reopened_config_recovery(reopened).unwrap()
        fresh_proof.close()


def test_recovery_decoder_rejects_old_or_tampered_records(tmp_path: Path) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-reopen-decode"),
        ).unwrap()
        record = prepared.recovery_descriptor.to_record()
        legacy = dict(record)
        legacy.pop("descriptor_version")
        assert (
            _code(atomic_file.decode_config_recovery_descriptor(legacy))
            == "config.external_change"
        )
        tampered = dict(record)
        tampered["candidate_byte_digest"] = "0" * 64
        assert (
            _code(atomic_file.decode_config_recovery_descriptor(tampered))
            == "config.external_change"
        )
        assert os.fspath(tmp_path) not in repr(record)
        prepared.close()


@pytest.mark.parametrize(
    "mutation",
    (
        "missing-persistent-backup",
        "reused-stage-identity",
        "reused-stage-metadata",
        "reused-backup-identity",
        "reused-backup-metadata",
        "absent-before-with-mode",
        "absent-before-with-displaced",
        "before-reuses-candidate-identity",
    ),
)
def test_recovery_decoder_rejects_recomputed_impossible_stage_records(
    tmp_path: Path,
    mutation: str,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.contracts import canonical_json_bytes

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(
                snapshot,
                identity,
                source,
                persistent_backup=True,
            ),
            atomic_file.begin_config_transaction(f"tx-impossible-{mutation}"),
        ).unwrap()
        record = prepared.recovery_descriptor.to_record()
        if mutation == "missing-persistent-backup":
            record["backup_identity"] = None
            record["backup_stage_metadata_digest"] = None
        elif mutation == "reused-stage-identity":
            record["candidate_identity"] = record["snapshot_identity"]
        elif mutation == "reused-stage-metadata":
            record["candidate_stage_metadata_digest"] = record[
                "snapshot_stage_metadata_digest"
            ]
        elif mutation == "reused-backup-identity":
            record["backup_identity"] = record["snapshot_identity"]
        elif mutation == "reused-backup-metadata":
            record["backup_stage_metadata_digest"] = record[
                "snapshot_stage_metadata_digest"
            ]
        elif mutation == "absent-before-with-mode":
            record["before_identity"] = None
            record["before_mode"] = 0o600
        elif mutation == "absent-before-with-displaced":
            record["before_identity"] = None
            record["before_mode"] = None
            record["displaced_identity"] = (101, 202)
        else:
            record["before_identity"] = record["candidate_identity"]
        domain = dict(record)
        domain.pop("descriptor_digest")
        record["descriptor_digest"] = hashlib.sha256(
            canonical_json_bytes(domain)
        ).hexdigest()

        assert (
            _code(atomic_file.decode_config_recovery_descriptor(record))
            == "config.external_change"
        )
        prepared.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX quarantine recovery contract")
def test_reopened_cleanup_resumes_deterministic_quarantine(tmp_path: Path) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-reopen-quarantine"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        descriptor = committed.recovery_descriptor
        prepared.close()
        quarantine = home / atomic_file._cleanup_reference(
            descriptor.snapshot_reference
        )
        (home / descriptor.snapshot_reference).rename(quarantine)
        fresh_proof = authority.prove_config_path(root).unwrap()
        try:
            reopened = atomic_file.reopen_config_recovery(
                fresh_proof,
                atomic_file.decode_config_recovery_descriptor(
                    descriptor.to_record()
                ).unwrap(),
            ).unwrap()
            atomic_file.cleanup_reopened_config_recovery(reopened).unwrap()
        finally:
            fresh_proof.close()
        assert not quarantine.exists()


def test_reopened_cleanup_closes_source_when_quarantine_open_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-reopen-close-on-open-fault"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        descriptor = committed.recovery_descriptor
        prepared.close()
        fresh_proof = authority.prove_config_path(root).unwrap()
        reopened = atomic_file.reopen_config_recovery(
            fresh_proof,
            atomic_file.decode_config_recovery_descriptor(
                descriptor.to_record()
            ).unwrap(),
        ).unwrap()
        original_open = atomic_file._open_recovery_name
        original_close = atomic_file._close_descriptor
        quarantine = atomic_file._cleanup_reference(descriptor.snapshot_reference)
        opened_source = 0 if os.name == "nt" else -1
        source_opened = False
        source_closed = False

        def fail_quarantine_open(
            parent: int,
            name: str,
            *,
            delete: bool = False,
        ) -> int:
            nonlocal opened_source, source_opened
            if name == quarantine:
                raise OSError("injected quarantine open failure")
            opened = original_open(parent, name, delete=delete)
            if name == descriptor.snapshot_reference:
                opened_source = opened
                source_opened = True
            return opened

        def track_close(opened: int) -> None:
            nonlocal source_closed
            if source_opened and opened == opened_source:
                source_closed = True
            original_close(opened)

        with monkeypatch.context() as scoped:
            scoped.setattr(atomic_file, "_open_recovery_name", fail_quarantine_open)
            scoped.setattr(atomic_file, "_close_descriptor", track_close)
            assert (
                _code(atomic_file.cleanup_reopened_config_recovery(reopened))
                == "config.commit_ambiguous"
            )
        if not source_closed:
            original_close(opened_source)
        assert source_closed
        atomic_file.cleanup_reopened_config_recovery(reopened).unwrap()
        fresh_proof.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX identity substitution fixture")
def test_reopen_rejects_and_preserves_replaced_recovery_stage(tmp_path: Path) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-reopen-tamper"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        descriptor = committed.recovery_descriptor
        prepared.close()
        stage = home / descriptor.snapshot_reference
        retained = home / "third-party-retained.snapshot"
        stage.rename(retained)
        stage.write_bytes(raw)
        stage.chmod(0o600)
        fresh_proof = authority.prove_config_path(root).unwrap()
        try:
            result = atomic_file.reopen_config_recovery(
                fresh_proof,
                atomic_file.decode_config_recovery_descriptor(
                    descriptor.to_record()
                ).unwrap(),
            )
            assert _code(result) == "config.external_change"
        finally:
            fresh_proof.close()
        assert retained.read_bytes() == raw
        assert stage.read_bytes() == raw


def test_reopened_recovery_rolls_back_exact_before_state(tmp_path: Path) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-reopen-rollback"),
        ).unwrap()
        record = prepared.recovery_descriptor.to_record()
        atomic_file.acknowledge_config_preparation(prepared).unwrap()
        prepared._retain()
        prepared.close()
        fresh_proof = authority.prove_config_path(root).unwrap()
        try:
            reopened = atomic_file.reopen_config_recovery(
                fresh_proof,
                atomic_file.decode_config_recovery_descriptor(record).unwrap(),
            ).unwrap()
            assert (
                atomic_file.classify_reopened_config_recovery(reopened).unwrap()
                is atomic_file.ConfigCommitState.BEFORE
            )
            atomic_file.rollback_reopened_config_recovery(reopened).unwrap()
        finally:
            fresh_proof.close()
        assert (home / "config.toml").read_bytes() == raw
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


@pytest.mark.skipif(os.name == "nt", reason="POSIX candidate mode transition fixture")
def test_reopen_accepts_candidate_metadata_applied_before_replace(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw, mode=0o644) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-reopen-metadata-transition"),
        ).unwrap()
        descriptor = prepared.recovery_descriptor
        atomic_file._apply_candidate_metadata(prepared)
        prepared._retain()
        prepared.close()
        fresh_proof = authority.prove_config_path(root).unwrap()
        try:
            reopened = atomic_file.reopen_config_recovery(
                fresh_proof,
                atomic_file.decode_config_recovery_descriptor(
                    descriptor.to_record()
                ).unwrap(),
            ).unwrap()
            assert (
                atomic_file.classify_reopened_config_recovery(reopened).unwrap()
                is atomic_file.ConfigCommitState.BEFORE
            )
            atomic_file.rollback_reopened_config_recovery(reopened).unwrap()
        finally:
            fresh_proof.close()


def test_reopened_recovery_promotes_exact_persistent_backup(tmp_path: Path) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source, persistent_backup=True),
            atomic_file.begin_config_transaction("tx-reopen-backup"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        record = committed.recovery_descriptor.to_record()
        prepared.close()
        decoded = atomic_file.decode_config_recovery_descriptor(record).unwrap()
        fresh_proof = authority.prove_config_path(root).unwrap()
        try:
            reopened = atomic_file.reopen_config_recovery(
                fresh_proof,
                decoded,
            ).unwrap()
            backup = atomic_file.promote_reopened_config_backup(reopened).unwrap()
            assert backup is not None
            assert (home / backup.relative_path).read_bytes() == raw
            atomic_file.cleanup_reopened_config_recovery(reopened).unwrap()
        finally:
            fresh_proof.close()


def test_reopened_backup_retry_syncs_already_promoted_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        root,
        authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source, persistent_backup=True),
            atomic_file.begin_config_transaction("tx-reopen-backup-sync"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        descriptor = committed.recovery_descriptor
        prepared.close()
        fresh_proof = authority.prove_config_path(root).unwrap()
        reopened = atomic_file.reopen_config_recovery(
            fresh_proof,
            atomic_file.decode_config_recovery_descriptor(
                descriptor.to_record()
            ).unwrap(),
        ).unwrap()
        try:
            if os.name == "nt":
                original_rename = atomic_file._paths._windows_rename_handle

                def rename_then_fail(
                    opened: int,
                    parent: int,
                    destination: str,
                ) -> None:
                    original_rename(opened, parent, destination)
                    raise OSError("injected post-rename failure")

                target = atomic_file._paths
                attribute = "_windows_rename_handle"
            else:
                original_rename = atomic_file._paths._exclusive_posix_rename

                def rename_then_fail(
                    parent: int,
                    source_name: str,
                    destination: str,
                ) -> None:
                    original_rename(parent, source_name, destination)
                    raise OSError("injected post-rename failure")

                target = atomic_file._paths
                attribute = "_exclusive_posix_rename"
            with monkeypatch.context() as scoped:
                scoped.setattr(target, attribute, rename_then_fail)
                assert (
                    _code(atomic_file.promote_reopened_config_backup(reopened))
                    == "config.commit_ambiguous"
                )
            assert (home / descriptor.backup_reference).is_file()
            synced: list[int] = []
            with monkeypatch.context() as scoped:
                scoped.setattr(
                    atomic_file,
                    "_sync_parent",
                    lambda parent: synced.append(parent),
                )
                atomic_file.promote_reopened_config_backup(reopened).unwrap()
            assert len(synced) == 1
            atomic_file.cleanup_reopened_config_recovery(reopened).unwrap()
        finally:
            reopened.close()
            fresh_proof.close()


def _write_spawned_config_recovery_fixture(
    workspace: str,
    record_path: str,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(Path(workspace), raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-spawned-reopen"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        Path(record_path).write_text(
            json.dumps(committed.recovery_descriptor.to_record()),
            encoding="utf-8",
        )
        prepared.close()


def test_spawned_process_death_reopens_committed_config_recovery(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.paths import PlatformPathAuthority

    workspace = tmp_path / "spawned-recovery"
    workspace.mkdir()
    record_path = workspace / "recovery.json"
    command = (
        "import runpy,sys; "
        "module=runpy.run_path(sys.argv[1]); "
        "module['_write_spawned_config_recovery_fixture'](sys.argv[2],sys.argv[3])"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            command,
            os.fspath(Path(__file__).resolve()),
            os.fspath(workspace),
            os.fspath(record_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(
        workspace / "codex-home",
        runner=_runner(),
    ).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        descriptor = atomic_file.decode_config_recovery_descriptor(
            json.loads(record_path.read_text(encoding="utf-8"))
        ).unwrap()
        reopened = atomic_file.reopen_config_recovery(proof, descriptor).unwrap()
        assert (
            atomic_file.classify_reopened_config_recovery(reopened).unwrap()
            is atomic_file.ConfigCommitState.CANDIDATE
        )
        atomic_file.cleanup_reopened_config_recovery(reopened).unwrap()
    finally:
        proof.close()
        root.close()


def _kill_after_fsynced_preparation_stage(
    workspace: str,
    barrier: str,
    persistent_backup: bool,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(Path(workspace), raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        original_populate = atomic_file._populate_stage
        stage_roles = (
            ("snapshot", "backup", "candidate")
            if persistent_backup
            else ("snapshot", "candidate")
        )
        calls = 0

        def populate_then_exit(descriptor: int, staged: bytes, *, mode: int) -> None:
            nonlocal calls
            original_populate(descriptor, staged, mode=mode)
            role = stage_roles[calls]
            calls += 1
            if role == barrier:
                os._exit(91)

        if barrier != "prepared-return":
            setattr(atomic_file, "_populate_stage", populate_then_exit)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(
                snapshot,
                identity,
                source,
                persistent_backup=persistent_backup,
            ),
            atomic_file.begin_config_transaction(f"tx-kill-{barrier}"),
        ).unwrap()
        if barrier == "prepared-return":
            os._exit(91)
        prepared.close()
        os._exit(92)


def _run_fsynced_preparation_death(
    workspace: Path,
    *,
    barrier: str,
    persistent_backup: bool,
) -> subprocess.CompletedProcess[str]:
    command = (
        "import runpy,sys; "
        "module=runpy.run_path(sys.argv[1]); "
        "module['_kill_after_fsynced_preparation_stage']"
        "(sys.argv[2],sys.argv[3],sys.argv[4]=='true')"
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            command,
            os.fspath(Path(__file__).resolve()),
            os.fspath(workspace),
            barrier,
            str(persistent_backup).lower(),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _kill_during_preparation_checkpoint_rotation(workspace: str) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(Path(workspace), raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        original_unlink = atomic_file._unlink_owned

        def exit_before_old_authority_unlink(
            parent: int,
            name: str,
            descriptor: int,
            expected_identity: tuple[int, int],
            *,
            allow_moved: bool = False,
        ) -> bool:
            if name.endswith(".snapshot.authority"):
                os._exit(93)
            return original_unlink(
                parent,
                name,
                descriptor,
                expected_identity,
                allow_moved=allow_moved,
            )

        setattr(atomic_file, "_unlink_owned", exit_before_old_authority_unlink)
        atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-kill-authority-rotation"),
        ).unwrap()
        os._exit(94)


def _run_checkpoint_rotation_death(
    workspace: Path,
) -> subprocess.CompletedProcess[str]:
    command = (
        "import runpy,sys; "
        "module=runpy.run_path(sys.argv[1]); "
        "module['_kill_during_preparation_checkpoint_rotation'](sys.argv[2])"
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            command,
            os.fspath(Path(__file__).resolve()),
            os.fspath(workspace),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _exit_during_live_preparation_cleanup(workspace: str) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(Path(workspace), raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        original_unlink = atomic_file._unlink_owned

        def fail_readiness(_prepared: Any) -> None:
            raise atomic_file._error(
                "config.external_change",
                "Injected final readiness failure.",
            )

        def exit_after_snapshot_unlink(
            parent: int,
            name: str,
            descriptor: int,
            expected_identity: tuple[int, int],
            *,
            allow_moved: bool = False,
        ) -> bool:
            removed = original_unlink(
                parent,
                name,
                descriptor,
                expected_identity,
                allow_moved=allow_moved,
            )
            if removed and name.endswith(".snapshot"):
                os._exit(95)
            return removed

        setattr(atomic_file.PreparedAtomicFile, "_require_ready", fail_readiness)
        setattr(atomic_file, "_unlink_owned", exit_after_snapshot_unlink)
        atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-live-cleanup-death"),
        ).unwrap()
        os._exit(96)


@pytest.mark.parametrize(
    ("barrier", "persistent_backup"),
    (
        ("snapshot", False),
        ("backup", True),
        ("candidate", False),
        ("prepared-return", False),
    ),
)
def test_fsynced_stage_process_death_uses_only_precrash_recovery_record(
    tmp_path: Path,
    barrier: str,
    persistent_backup: bool,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.paths import PlatformPathAuthority

    workspace = tmp_path / f"kill-{barrier}"
    workspace.mkdir()
    completed = _run_fsynced_preparation_death(
        workspace,
        barrier=barrier,
        persistent_backup=persistent_backup,
    )
    assert completed.returncode == 91, completed.stderr
    home = workspace / "codex-home"
    records = tuple(home.glob(".zagrosi-config-tx-*.authority"))
    assert len(records) == 1
    decoded = tuple(
        atomic_file.decode_config_preparation_recovery_descriptor(
            json.loads(record.read_text(encoding="utf-8"))
        ).unwrap()
        for record in records
    )
    descriptor = max(decoded, key=lambda item: len(item.stages))
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        atomic_file.cleanup_restarted_config_preparation(
            proof,
            descriptor,
        ).unwrap()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())
        assert (home / "config.toml").read_bytes() == _fixture("cases/preserve.toml")
    finally:
        proof.close()
        root.close()


def test_restarted_preparation_cleanup_prevalidates_every_stage(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.paths import PlatformPathAuthority

    workspace = tmp_path / "prevalidate-all-stages"
    workspace.mkdir()
    completed = _run_fsynced_preparation_death(
        workspace,
        barrier="prepared-return",
        persistent_backup=True,
    )
    assert completed.returncode == 91, completed.stderr
    home = workspace / "codex-home"
    authority_path = next(home.glob(".zagrosi-config-tx-*.authority"))
    descriptor = atomic_file.decode_config_preparation_recovery_descriptor(
        json.loads(authority_path.read_text(encoding="utf-8"))
    ).unwrap()
    candidate_reference = descriptor.stages[-1][1]
    candidate_path = home / candidate_reference
    retained_candidate = home / "retained-candidate"
    candidate_path.rename(retained_candidate)
    unknown = _write_private_test_file(home, candidate_reference, b"unknown-candidate")
    stage_paths = tuple(
        home / reference for _role, reference, _identity in descriptor.stages
    )

    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        observed = atomic_file.cleanup_restarted_config_preparation(proof, descriptor)
        assert _code(observed) == "config.external_change"
        assert all(path.exists() for path in stage_paths[:-1])
        assert unknown.read_bytes() == b"unknown-candidate"
        assert retained_candidate.is_file()
        assert authority_path.is_file()
    finally:
        proof.close()
        root.close()


def test_restarted_preparation_cleanup_revalidates_each_stage_during_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.paths import PlatformPathAuthority

    workspace = tmp_path / "revalidate-stages"
    workspace.mkdir()
    completed = _run_fsynced_preparation_death(
        workspace,
        barrier="prepared-return",
        persistent_backup=False,
    )
    assert completed.returncode == 91, completed.stderr
    home = workspace / "codex-home"
    authority_path = next(home.glob(".zagrosi-config-tx-*.authority"))
    descriptor = atomic_file.decode_config_preparation_recovery_descriptor(
        json.loads(authority_path.read_text(encoding="utf-8"))
    ).unwrap()
    identities = {
        identity
        for _role, _reference, identity in descriptor.stages
        if identity is not None
    }
    validations = {identity: 0 for identity in identities}
    original_require = atomic_file._require_private_preparation_file

    def count_validation(opened: int, identity: tuple[int, int]) -> None:
        original_require(opened, identity)
        if identity in validations:
            validations[identity] += 1

    monkeypatch.setattr(
        atomic_file,
        "_require_private_preparation_file",
        count_validation,
    )
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        atomic_file.cleanup_restarted_config_preparation(proof, descriptor).unwrap()
        assert validations
        assert all(count >= 2 for count in validations.values())
    finally:
        proof.close()
        root.close()


def test_restarted_preparation_cleanup_fsyncs_stages_before_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.paths import PlatformPathAuthority

    workspace = tmp_path / "cleanup-sync-order"
    workspace.mkdir()
    completed = _run_fsynced_preparation_death(
        workspace,
        barrier="prepared-return",
        persistent_backup=False,
    )
    assert completed.returncode == 91, completed.stderr
    home = workspace / "codex-home"
    authority_path = next(home.glob(".zagrosi-config-tx-*.authority"))
    descriptor = atomic_file.decode_config_preparation_recovery_descriptor(
        json.loads(authority_path.read_text(encoding="utf-8"))
    ).unwrap()
    events: list[str] = []
    original_unlink = atomic_file._unlink_owned
    original_sync = atomic_file._sync_parent

    def record_unlink(
        parent: int,
        name: str,
        opened: int,
        identity: tuple[int, int],
        *,
        allow_moved: bool = False,
    ) -> bool:
        events.append(f"unlink:{name}")
        return original_unlink(
            parent,
            name,
            opened,
            identity,
            allow_moved=allow_moved,
        )

    def record_sync(parent: int) -> None:
        events.append("sync")
        original_sync(parent)

    monkeypatch.setattr(atomic_file, "_unlink_owned", record_unlink)
    monkeypatch.setattr(atomic_file, "_sync_parent", record_sync)
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        atomic_file.cleanup_restarted_config_preparation(proof, descriptor).unwrap()
        syncs = [index for index, event in enumerate(events) if event == "sync"]
        stage_unlinks = [
            index
            for index, event in enumerate(events)
            if any(
                event == f"unlink:{reference}"
                for _role, reference, _identity in descriptor.stages
            )
        ]
        authority_unlink = events.index(f"unlink:{descriptor.authority_reference}")
        assert len(syncs) == 2
        assert max(stage_unlinks) < syncs[0] < authority_unlink < syncs[1]
    finally:
        proof.close()
        root.close()


def test_checkpoint_rotation_process_death_reconciles_superseded_authority(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.paths import PlatformPathAuthority

    workspace = tmp_path / "authority-rotation-death"
    workspace.mkdir()
    completed = _run_checkpoint_rotation_death(workspace)
    assert completed.returncode == 93, completed.stderr
    home = workspace / "codex-home"
    records = tuple(home.glob(".zagrosi-config-tx-*.authority"))
    assert len(records) == 2
    decoded = tuple(
        atomic_file.decode_config_preparation_recovery_descriptor(
            json.loads(record.read_text(encoding="utf-8"))
        ).unwrap()
        for record in records
    )
    descriptor = max(decoded, key=lambda item: len(item.stages))
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        atomic_file.cleanup_restarted_config_preparation(proof, descriptor).unwrap()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())
    finally:
        proof.close()
        root.close()


def test_restarted_cleanup_rejects_unbound_self_consistent_predecessor(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.paths import PlatformPathAuthority

    workspace = tmp_path / "forged-predecessor"
    workspace.mkdir()
    completed = _run_checkpoint_rotation_death(workspace)
    assert completed.returncode == 93, completed.stderr
    home = workspace / "codex-home"
    records = tuple(home.glob(".zagrosi-config-tx-*.authority"))
    decoded = tuple(
        atomic_file.decode_config_preparation_recovery_descriptor(
            json.loads(record.read_text(encoding="utf-8"))
        ).unwrap()
        for record in records
    )
    newest = max(decoded, key=lambda item: len(item.stages))
    predecessor = min(decoded, key=lambda item: len(item.stages))
    assert predecessor.authority_reference is not None
    predecessor_path = home / predecessor.authority_reference
    retained_predecessor = home / "retained-predecessor"
    predecessor_path.rename(retained_predecessor)
    forged_path = _write_private_test_file(home, predecessor.authority_reference, b"")
    status = forged_path.stat(follow_symlinks=False)
    forged_identity = (status.st_dev, status.st_ino)
    forged = atomic_file.ConfigPreparationRecoveryDescriptor(
        transaction_digest=newest.transaction_digest,
        parent_identity=newest.parent_identity,
        authority_reference=predecessor.authority_reference,
        authority_identity=forged_identity,
        stages=newest.stages[:1],
        _token=atomic_file._PREPARATION_DESCRIPTOR_TOKEN,
    )
    forged_path.write_bytes(canonical_json_bytes(forged.to_record()))
    if os.name != "nt":
        forged_path.chmod(0o600)
    stage_paths = tuple(
        home / reference for _role, reference, _identity in newest.stages
    )
    newest_path = home / str(newest.authority_reference)

    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        observed = atomic_file.cleanup_restarted_config_preparation(proof, newest)
        assert _code(observed) == "config.external_change"
        assert all(path.is_file() for path in stage_paths)
        assert forged_path.is_file()
        assert retained_predecessor.is_file()
        assert newest_path.is_file()
    finally:
        proof.close()
        root.close()


def test_prepared_close_prevalidates_candidate_before_deleting_recovery(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.contracts import ForgeError

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-close-prevalidation"),
        ).unwrap()
        snapshot_path = home / prepared.snapshot_reference
        candidate_path = home / prepared.candidate_reference
        retained_candidate = home / "retained-close-candidate"
        candidate_path.rename(retained_candidate)
        unknown = _write_private_test_file(
            home,
            prepared.candidate_reference,
            b"unknown-close-candidate",
        )
        authority_path = next(home.glob(".zagrosi-config-tx-*.authority"))

        with pytest.raises(ForgeError) as raised:
            prepared.close()
        assert raised.value.code == "config.commit_ambiguous"
        assert snapshot_path.is_file()
        assert retained_candidate.is_file()
        assert unknown.read_bytes() == b"unknown-close-candidate"
        assert authority_path.is_file()


def test_commit_requires_successful_preparation_acknowledgment(tmp_path: Path) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-commit-requires-ack"),
        ).unwrap()
        assert (
            _code(atomic_file.commit_atomic_candidate(prepared, expected=snapshot))
            == "config.commit_ambiguous"
        )
        assert (home / "config.toml").read_bytes() == raw
        atomic_file.acknowledge_config_preparation(prepared).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        atomic_file.cleanup_config_recovery(committed).unwrap()


@pytest.mark.parametrize("boundary", ("close-before", "close-after", "sync"))
def test_preparation_acknowledgment_is_retry_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction(f"tx-ack-retry-{boundary}"),
        ).unwrap()
        original_close = atomic_file._close_descriptor
        original_sync = atomic_file._sync_parent
        injected = False

        def fail_close(descriptor: int) -> None:
            nonlocal injected
            if (
                descriptor == prepared._preparation_authority_descriptor
                and not injected
            ):
                injected = True
                if boundary == "close-after":
                    original_close(descriptor)
                raise OSError("injected authority close ambiguity")
            original_close(descriptor)

        def fail_sync(parent: int) -> None:
            nonlocal injected
            if not injected:
                injected = True
                raise OSError("injected authority sync ambiguity")
            original_sync(parent)

        if boundary == "sync":
            monkeypatch.setattr(atomic_file, "_sync_parent", fail_sync)
        else:
            monkeypatch.setattr(atomic_file, "_close_descriptor", fail_close)
        first = atomic_file.acknowledge_config_preparation(prepared)
        if not first.is_ok:
            assert _code(first) == "config.commit_ambiguous"
        monkeypatch.setattr(atomic_file, "_close_descriptor", original_close)
        monkeypatch.setattr(atomic_file, "_sync_parent", original_sync)
        atomic_file.acknowledge_config_preparation(prepared).unwrap()
        prepared.close()


def test_preparation_authority_releases_only_after_explicit_acknowledgment(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.contracts import canonical_json_bytes

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-explicit-preparation-ack"),
        ).unwrap()
        assert len(tuple(home.glob(".zagrosi-config-tx-*.authority"))) == 1
        journal = tmp_path / "prepared-record.json"
        journal.write_bytes(
            canonical_json_bytes(prepared.recovery_descriptor.to_record())
        )
        with journal.open("rb") as descriptor:
            os.fsync(descriptor.fileno())

        assert (
            atomic_file.acknowledge_config_preparation(prepared).unwrap()
            == prepared.recovery_descriptor
        )
        assert not tuple(home.glob(".zagrosi-config-tx-*.authority"))
        assert (home / prepared.snapshot_reference).is_file()
        assert (home / prepared.candidate_reference).is_file()
        prepared.close()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


def test_private_snapshot_exclusive_open_collision_preserves_unknown_file(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    occupied = b"third-party-stage"
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        transaction = atomic_file.begin_config_transaction("tx-snapshot-collision")
        _write_private_test_file(home, transaction.snapshot_reference, occupied)
        result = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            transaction,
        )
        assert _code(result) == "config.atomic_write_failed"
        assert (home / transaction.snapshot_reference).read_bytes() == occupied
        assert not (home / transaction.candidate_reference).exists()
        assert not (home / transaction.backup_stage_reference).exists()
        assert (home / "config.toml").read_bytes() == raw
        assert proof.leaf_identity == snapshot.leaf_identity


@pytest.mark.parametrize("boundary", ["write", "sync", "validation"])
def test_short_write_flush_fsync_or_validation_failure_preserves_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        if boundary == "write":
            if os.name == "nt":
                original_windows = atomic_file._ownership._windows_write_all

                def fail_windows_candidate(descriptor: int, value: bytes) -> None:
                    if value != raw and value:
                        raise OSError("short write")
                    original_windows(descriptor, value)

                monkeypatch.setattr(
                    atomic_file._ownership,
                    "_windows_write_all",
                    fail_windows_candidate,
                )
            else:
                original = atomic_file._write_all

                def fail_candidate(descriptor: int, value: bytes) -> None:
                    if value != raw and value:
                        raise OSError("short write")
                    original(descriptor, value)

                monkeypatch.setattr(atomic_file, "_write_all", fail_candidate)
        elif boundary == "sync":
            if os.name == "nt":
                monkeypatch.setattr(
                    atomic_file._ownership,
                    "_windows_flush",
                    lambda _descriptor: (_ for _ in ()).throw(OSError("flush")),
                )
            else:
                monkeypatch.setattr(
                    atomic_file,
                    "_sync_file",
                    lambda _descriptor: (_ for _ in ()).throw(OSError("fsync")),
                )
        else:
            monkeypatch.setattr(
                atomic_file, "_validate_prepared_candidate", lambda *_args: False
            )
        result = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction(f"tx-{boundary}"),
        )
        assert _code(result) == "config.atomic_write_failed"
        assert (home / "config.toml").read_bytes() == raw


def test_candidate_metadata_apply_failure_preserves_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-metadata-apply-fault"),
        ).unwrap()
        with prepared:
            monkeypatch.setattr(
                atomic_file,
                "_apply_candidate_metadata",
                lambda _prepared: (_ for _ in ()).throw(
                    OSError("injected metadata application failure")
                ),
            )
            committed = _commit_acknowledged(
                prepared,
                expected=snapshot,
            ).unwrap()
            assert committed.state is atomic_file.ConfigCommitState.BEFORE
            assert committed.error_code == "config.atomic_write_failed"
            assert (home / "config.toml").read_bytes() == raw
            assert proof.leaf_identity == snapshot.leaf_identity
            atomic_file.rollback_config_recovery(committed).unwrap()


def test_replace_failure_preserves_original_or_classifies_actual_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-replace-fail"),
        ).unwrap()
        with prepared:
            monkeypatch.setattr(
                atomic_file,
                "_atomic_replace",
                lambda _prepared: (_ for _ in ()).throw(OSError("replace")),
            )
            result = _commit_acknowledged(prepared, expected=snapshot).unwrap()
            assert result.state is atomic_file.ConfigCommitState.BEFORE
            assert result.error_code == "config.atomic_write_failed"
            assert (home / "config.toml").read_bytes() == raw


def test_fault_after_candidate_publication_is_never_clean_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-post-publication-fault"),
        ).unwrap()
        original_replace = atomic_file._atomic_replace

        def replace_then_fail(value: Any) -> None:
            original_replace(value)
            raise OSError("post-publication fault")

        with prepared:
            monkeypatch.setattr(atomic_file, "_atomic_replace", replace_then_fail)
            committed = _commit_acknowledged(
                prepared,
                expected=snapshot,
            ).unwrap()
            assert committed.state is atomic_file.ConfigCommitState.CANDIDATE
            assert not committed.durability_confirmed
            assert committed.error_code == "config.commit_ambiguous"


@pytest.mark.parametrize("corruption", ["digest", "identity"])
def test_post_replace_digest_or_identity_corruption_is_third_party_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction(f"tx-post-{corruption}"),
        ).unwrap()
        original_replace = atomic_file._atomic_replace
        candidate_bytes = atomic_file._candidate_bytes(candidate)

        def replace_then_corrupt(value: Any) -> None:
            original_replace(value)
            config = home / "config.toml"
            if corruption == "digest":
                changed = bytearray(candidate_bytes)
                changed[0] = changed[0] ^ 1
                with config.open("r+b") as stream:
                    stream.write(changed)
                    stream.flush()
                    os.fsync(stream.fileno())
                return
            temporary = _write_private_test_file(
                home,
                "third-party-after-replace.tmp",
                candidate_bytes,
            )
            os.replace(temporary, config)

        with prepared:
            monkeypatch.setattr(
                atomic_file,
                "_atomic_replace",
                replace_then_corrupt,
            )
            committed = _commit_acknowledged(
                prepared,
                expected=snapshot,
            ).unwrap()
            assert committed.state is atomic_file.ConfigCommitState.THIRD_PARTY
            assert not committed.durability_confirmed
            assert committed.error_code == "config.commit_ambiguous"
            assert (
                _code(atomic_file.cleanup_config_recovery(committed))
                == "config.external_change"
            )
            if corruption == "digest":
                assert (home / "config.toml").read_bytes() != candidate_bytes
            else:
                assert (home / "config.toml").read_bytes() == candidate_bytes


def test_intervening_config_value_is_never_lost_by_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    third = b"third_party = true\n"
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-intervening-value"),
        ).unwrap()
        original_replace = atomic_file._atomic_replace

        def replace_after_intervening_write(value: Any) -> None:
            temporary = home / "third-party.tmp"
            temporary.write_bytes(third)
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, home / "config.toml")
            original_replace(value)

        with prepared:
            monkeypatch.setattr(
                atomic_file,
                "_atomic_replace",
                replace_after_intervening_write,
            )
            committed = _commit_acknowledged(
                prepared,
                expected=snapshot,
            ).unwrap()
            assert not (
                committed.state is atomic_file.ConfigCommitState.CANDIDATE
                and committed.error_code is None
            )
        retained = [
            item.read_bytes()
            for item in home.iterdir()
            if item.is_file() and item.stat().st_size <= 4096
        ]
        assert third in retained


def test_post_replace_metadata_drift_is_not_clean_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        return

    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-post-metadata"),
        ).unwrap()
        original_replace = atomic_file._atomic_replace

        def replace_then_weaken(value: Any) -> None:
            original_replace(value)
            (home / "config.toml").chmod(0o666)

        with prepared:
            monkeypatch.setattr(atomic_file, "_atomic_replace", replace_then_weaken)
            committed = _commit_acknowledged(
                prepared,
                expected=snapshot,
            ).unwrap()
            assert not committed.durability_confirmed
            assert committed.error_code == "config.commit_ambiguous"


def test_post_replace_parent_security_drift_is_not_clean_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        return

    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-parent-metadata-drift"),
        ).unwrap()
        original_replace = atomic_file._atomic_replace

        def replace_then_weaken_parent(value: Any) -> None:
            original_replace(value)
            home.chmod(0o777)

        try:
            with prepared:
                monkeypatch.setattr(
                    atomic_file,
                    "_atomic_replace",
                    replace_then_weaken_parent,
                )
                result = _commit_acknowledged(
                    prepared,
                    expected=snapshot,
                )
                assert _code(result) == "config.commit_ambiguous"
        finally:
            home.chmod(0o700)


def test_partial_replace_third_party_retains_all_recovery_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-partial-third"),
        ).unwrap()
        snapshot_path = home / prepared.snapshot_reference
        candidate_path = home / prepared.candidate_reference

        def write_third_then_fail(_prepared: Any) -> None:
            config = home / "config.toml"
            config.write_bytes(b"third_party = true\n")
            if os.name != "nt":
                config.chmod(0o600)
            raise OSError("partial replace")

        with prepared:
            monkeypatch.setattr(atomic_file, "_atomic_replace", write_third_then_fail)
            committed = _commit_acknowledged(prepared, expected=snapshot).unwrap()
            assert committed.state is atomic_file.ConfigCommitState.THIRD_PARTY
            assert committed.error_code == "config.commit_ambiguous"
            assert _code(atomic_file.cleanup_config_recovery(committed)) == (
                "config.external_change"
            )
        assert snapshot_path.read_bytes() == raw
        assert candidate_path.is_file()
        assert (home / "config.toml").read_bytes() == b"third_party = true\n"


def test_failed_commit_never_publishes_persistent_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source, persistent_backup=True)
        transaction = atomic_file.begin_config_transaction("tx-backup-fault")
        final_backup = home / transaction.backup_reference
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            transaction,
        ).unwrap()
        monkeypatch.setattr(
            atomic_file,
            "_atomic_replace",
            lambda _prepared: (_ for _ in ()).throw(OSError("replace")),
        )
        observed = _commit_acknowledged(prepared, expected=snapshot).unwrap()
        assert observed.state is atomic_file.ConfigCommitState.BEFORE
        assert not final_backup.exists()
        atomic_file.rollback_config_recovery(observed).unwrap()
        assert not final_backup.exists()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


def test_parent_directory_sync_failure_has_platform_specific_safe_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    with _config_context(tmp_path, None) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-parent-sync"),
        ).unwrap()
        atomic_file.acknowledge_config_preparation(prepared).unwrap()
        with prepared:
            monkeypatch.setattr(
                atomic_file,
                "_sync_parent",
                lambda _descriptor: (_ for _ in ()).throw(OSError("dir fsync")),
            )
            result = _commit_acknowledged(prepared, expected=snapshot).unwrap()
            assert result.state is atomic_file.ConfigCommitState.CANDIDATE
            assert not result.durability_confirmed
            assert result.error_code == "config.commit_ambiguous"
            assert (home / "config.toml").is_file()


def test_snapshot_cleanup_fault_is_reported_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-cleanup-retry"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        original_unlink_owned = atomic_file._unlink_owned

        def fail_snapshot(
            parent: int,
            name: str,
            descriptor: int,
            stage_identity: tuple[int, int],
            *,
            allow_moved: bool = False,
        ) -> bool:
            if name == committed.snapshot_reference:
                return False
            return original_unlink_owned(
                parent,
                name,
                descriptor,
                stage_identity,
                allow_moved=allow_moved,
            )

        with monkeypatch.context() as scoped:
            scoped.setattr(atomic_file, "_unlink_owned", fail_snapshot)
            assert _code(atomic_file.cleanup_config_recovery(committed)) == (
                "config.commit_ambiguous"
            )
            assert (home / committed.snapshot_reference).is_file()
        atomic_file.cleanup_config_recovery(committed).unwrap()
        assert not (home / committed.snapshot_reference).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX namespace race oracle")
def test_cleanup_quarantines_before_validating_a_mutable_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-cleanup-race"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        retained = home / ".expected-private-snapshot"
        quarantine = home / atomic_file._cleanup_reference(committed.snapshot_reference)
        original_rename = atomic_file._paths._exclusive_posix_rename
        swapped = False

        def swap_before_quarantine(parent: int, source_name: str, target: str) -> None:
            nonlocal swapped
            if not swapped and source_name == committed.snapshot_reference:
                (home / source_name).rename(retained)
                (home / source_name).write_bytes(b"third-party")
                swapped = True
            original_rename(parent, source_name, target)

        with monkeypatch.context() as scoped:
            scoped.setattr(
                atomic_file._paths,
                "_exclusive_posix_rename",
                swap_before_quarantine,
            )
            assert _code(atomic_file.cleanup_config_recovery(committed)) == (
                "config.commit_ambiguous"
            )
        assert swapped
        assert not (home / committed.snapshot_reference).exists()
        assert quarantine.read_bytes() == b"third-party"
        quarantine.unlink()
        retained.rename(home / committed.snapshot_reference)
        atomic_file.cleanup_config_recovery(committed).unwrap()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO substitution fixture")
def test_cleanup_reopens_swapped_fifo_nonblocking_and_preserves_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            _atomic_inputs(snapshot, identity, source),
            atomic_file.begin_config_transaction("tx-cleanup-fifo-swap"),
        ).unwrap()
        committed = _commit_acknowledged(
            prepared,
            expected=snapshot,
        ).unwrap()
        retained = home / ".expected-private-fifo-snapshot"
        quarantine_reference = atomic_file._cleanup_reference(
            committed.snapshot_reference
        )
        quarantine = home / quarantine_reference
        original_rename = atomic_file._paths._exclusive_posix_rename
        original_open = atomic_file.os.open
        swapped = False
        reopened_nonblocking = False

        def swap_fifo_before_quarantine(
            parent: int,
            source_name: str,
            target: str,
        ) -> None:
            nonlocal swapped
            if not swapped and source_name == committed.snapshot_reference:
                (home / source_name).rename(retained)
                os.mkfifo(home / source_name, mode=0o600)
                swapped = True
            original_rename(parent, source_name, target)

        def require_nonblocking_quarantine_open(
            path: str | bytes,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal reopened_nonblocking
            if path == quarantine_reference:
                assert flags & os.O_NONBLOCK
                reopened_nonblocking = True
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with monkeypatch.context() as scoped:
            scoped.setattr(
                atomic_file._paths,
                "_exclusive_posix_rename",
                swap_fifo_before_quarantine,
            )
            scoped.setattr(atomic_file.os, "open", require_nonblocking_quarantine_open)
            assert _code(atomic_file.cleanup_config_recovery(committed)) == (
                "config.commit_ambiguous"
            )
        assert swapped
        assert reopened_nonblocking
        assert not (home / committed.snapshot_reference).exists()
        assert stat.S_ISFIFO(quarantine.lstat().st_mode)
        assert retained.read_bytes() == raw
        quarantine.unlink()
        retained.rename(home / committed.snapshot_reference)
        atomic_file.cleanup_config_recovery(committed).unwrap()


@pytest.mark.skipif(os.name == "nt", reason="POSIX names can move independently")
def test_missing_stage_name_is_not_deletion_while_owned_inode_remains(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-cleanup-rename-away"),
        ).unwrap()
        moved = home / ".moved-private-snapshot"
        (home / prepared.snapshot_reference).rename(moved)
        try:
            assert not atomic_file._unlink_owned(
                prepared._parent_descriptor,
                prepared.snapshot_reference,
                prepared._snapshot_descriptor,
                prepared.snapshot_identity,
            )
            assert moved.read_bytes() == raw
        finally:
            moved.rename(home / prepared.snapshot_reference)
            prepared.close()


def test_no_backup_still_creates_private_transaction_snapshot(tmp_path: Path) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source, persistent_backup=False)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-no-backup"),
        ).unwrap()
        with prepared:
            assert prepared.backup_record is None
            assert (home / prepared.snapshot_reference).read_bytes() == raw


@pytest.mark.parametrize("role", ["snapshot", "candidate", "backup"])
def test_staged_config_bytes_are_revalidated_before_replace(
    tmp_path: Path,
    role: str,
) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(
            snapshot,
            identity,
            source,
            persistent_backup=role == "backup",
        )
        transaction = begin_config_transaction(f"tx-mutate-{role}")
        references = {
            "snapshot": transaction.snapshot_reference,
            "candidate": transaction.candidate_reference,
            "backup": transaction.backup_stage_reference,
        }
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            transaction,
        ).unwrap()
        with prepared:
            staged = home / references[role]
            staged.write_bytes(b"corrupt-stage")
            if os.name != "nt":
                staged.chmod(0o600)
            result = _commit_acknowledged(prepared, expected=snapshot)
            assert _code(result) == "config.external_change"
            assert (home / "config.toml").read_bytes() == raw


def test_staged_supported_metadata_change_is_not_normalized_away(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        return

    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-stage-metadata"),
        ).unwrap()
        with prepared:
            name = (
                b"com.zagrosi.spike"
                if sys.platform == "darwin"
                else b"user.zagrosi.spike"
            )
            _set_test_xattr(
                home / prepared.candidate_reference,
                name,
                b"tampered-after-prepare",
            )
            assert (
                _code(_commit_acknowledged(prepared, expected=snapshot))
                == "config.external_change"
            )
            assert (home / "config.toml").read_bytes() == raw


def test_final_prepared_readiness_failure_cleans_every_private_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)

        def fail_readiness(_prepared: Any) -> None:
            raise atomic_file._error(
                "config.external_change",
                "Injected final readiness failure.",
            )

        monkeypatch.setattr(
            atomic_file.PreparedAtomicFile,
            "_require_ready",
            fail_readiness,
        )
        result = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-final-readiness"),
        )
        assert _code(result) == "config.external_change"
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


def test_failed_preparation_cleanup_fault_is_reported_as_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        original_unlink_owned = atomic_file._unlink_owned

        def fail_readiness(_prepared: Any) -> None:
            raise atomic_file._error(
                "config.external_change",
                "Injected final readiness failure.",
            )

        def retain_snapshot(
            parent: int,
            name: str,
            descriptor: int,
            stage_identity: tuple[int, int],
            *,
            allow_moved: bool = False,
        ) -> bool:
            if name.endswith(".snapshot"):
                return False
            return original_unlink_owned(
                parent,
                name,
                descriptor,
                stage_identity,
                allow_moved=allow_moved,
            )

        with monkeypatch.context() as scoped:
            scoped.setattr(
                atomic_file.PreparedAtomicFile,
                "_require_ready",
                fail_readiness,
            )
            scoped.setattr(atomic_file, "_unlink_owned", retain_snapshot)
            result = atomic_file.prepare_atomic_candidate(
                proof,
                snapshot,
                candidate,
                atomic_file.begin_config_transaction("tx-prep-cleanup-fault"),
            )
        assert _code(result) == "config.commit_ambiguous"
        assert isinstance(result.error, atomic_file.ConfigPreparationError)
        record = result.error.recovery_descriptor.to_record()
        assert record["stages"]
        assert os.fspath(home) not in repr(record)
        assert "CANARY" not in repr(record)
        retained = [item for item in home.iterdir() if item.name.endswith(".snapshot")]
        assert len(retained) == 1
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as executor:
            cleanups = tuple(
                executor.map(
                    atomic_file.cleanup_config_preparation,
                    (result.error, result.error),
                )
            )
        records = tuple(cleanup.unwrap().to_record() for cleanup in cleanups)
        assert records[0] == records[1]
        assert (
            atomic_file.cleanup_config_preparation(result.error).unwrap().to_record()
            == records[0]
        )
        assert not retained[0].exists()


def test_failed_preparation_cleanup_preserves_newest_authority_on_unlink_ambiguity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        original_unlink = atomic_file._unlink_owned

        def fail_readiness(_prepared: Any) -> None:
            raise atomic_file._error(
                "config.external_change",
                "Injected final readiness failure.",
            )

        def retain_candidate(
            parent: int,
            name: str,
            descriptor: int,
            stage_identity: tuple[int, int],
            *,
            allow_moved: bool = False,
        ) -> bool:
            if name.endswith(".candidate"):
                return False
            return original_unlink(
                parent,
                name,
                descriptor,
                stage_identity,
                allow_moved=allow_moved,
            )

        with monkeypatch.context() as scoped:
            scoped.setattr(
                atomic_file.PreparedAtomicFile,
                "_require_ready",
                fail_readiness,
            )
            scoped.setattr(atomic_file, "_unlink_owned", retain_candidate)
            result = atomic_file.prepare_atomic_candidate(
                proof,
                snapshot,
                _atomic_inputs(snapshot, identity, source),
                atomic_file.begin_config_transaction("tx-live-cleanup-ambiguity"),
            )
        assert _code(result) == "config.commit_ambiguous"
        assert isinstance(result.error, atomic_file.ConfigPreparationError)
        assert any(item.name.endswith(".candidate") for item in home.iterdir())
        assert len(tuple(home.glob(".zagrosi-config-tx-*.authority"))) == 1
        atomic_file.cleanup_config_preparation(result.error).unwrap()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


def test_live_preparation_cleanup_process_death_retains_restart_authority(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file
    from zagrosi_forge.install.paths import PlatformPathAuthority

    workspace = tmp_path / "live-cleanup-death"
    workspace.mkdir()
    command = (
        "import runpy,sys; "
        "module=runpy.run_path(sys.argv[1]); "
        "module['_exit_during_live_preparation_cleanup'](sys.argv[2])"
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            command,
            os.fspath(Path(__file__).resolve()),
            os.fspath(workspace),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 95, completed.stderr
    home = workspace / "codex-home"
    authority_path = next(home.glob(".zagrosi-config-tx-*.authority"))
    descriptor = atomic_file.decode_config_preparation_recovery_descriptor(
        json.loads(authority_path.read_text(encoding="utf-8"))
    ).unwrap()
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        atomic_file.cleanup_restarted_config_preparation(proof, descriptor).unwrap()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())
    finally:
        proof.close()
        root.close()


def test_stage_write_failure_with_cleanup_fault_returns_recovery_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        original_unlink_owned = atomic_file._unlink_owned

        def retain_snapshot(
            parent: int,
            name: str,
            descriptor: int,
            stage_identity: tuple[int, int],
            *,
            allow_moved: bool = False,
        ) -> bool:
            if name.endswith(".snapshot"):
                return False
            return original_unlink_owned(
                parent,
                name,
                descriptor,
                stage_identity,
                allow_moved=allow_moved,
            )

        with monkeypatch.context() as scoped:
            if os.name == "nt":
                scoped.setattr(
                    atomic_file._ownership,
                    "_windows_flush",
                    lambda _descriptor: (_ for _ in ()).throw(
                        OSError("injected stage flush failure")
                    ),
                )
            else:
                scoped.setattr(
                    atomic_file,
                    "_sync_file",
                    lambda _descriptor: (_ for _ in ()).throw(
                        OSError("injected stage fsync failure")
                    ),
                )
            scoped.setattr(atomic_file, "_unlink_owned", retain_snapshot)
            result = atomic_file.prepare_atomic_candidate(
                proof,
                snapshot,
                candidate,
                atomic_file.begin_config_transaction("tx-stage-cleanup-fault"),
            )
        assert _code(result) == "config.commit_ambiguous"
        assert isinstance(result.error, atomic_file.ConfigPreparationError)
        assert any(item.name.endswith(".snapshot") for item in home.iterdir())
        recovery = result.error.recovery
        with pytest.raises(AttributeError):
            result.error._recovery = None
        object.__setattr__(result.error, "_recovery", None)
        assert (
            _code(atomic_file.cleanup_config_preparation(result.error))
            == "config.external_change"
        )
        object.__setattr__(result.error, "_recovery", recovery)
        atomic_file.cleanup_config_preparation(result.error).unwrap()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


def test_preparation_parent_sync_failure_removes_private_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        original_sync = atomic_file._sync_parent
        calls = 0

        def fail_first_sync(descriptor: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("injected staging directory sync failure")
            original_sync(descriptor)

        monkeypatch.setattr(atomic_file, "_sync_parent", fail_first_sync)
        result = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-prep-sync-fault"),
        )
        assert _code(result) == "config.atomic_write_failed"
        assert calls >= 2
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


def test_persistent_preparation_cleanup_sync_failure_retains_parent_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        with monkeypatch.context() as scoped:
            scoped.setattr(
                atomic_file,
                "_sync_parent",
                lambda _descriptor: (_ for _ in ()).throw(
                    OSError("injected persistent directory sync failure")
                ),
            )
            result = atomic_file.prepare_atomic_candidate(
                proof,
                snapshot,
                candidate,
                atomic_file.begin_config_transaction("tx-prep-sync-recovery"),
            )
        assert _code(result) == "config.commit_ambiguous"
        assert isinstance(result.error, atomic_file.ConfigPreparationError)
        assert len(tuple(home.glob(".zagrosi-config-tx-*.authority"))) == 1
        atomic_file.cleanup_config_preparation(result.error).unwrap()
        atomic_file.cleanup_config_preparation(result.error).unwrap()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())
        assert (home / "config.toml").read_bytes() == raw


def test_preparation_recovery_construction_fails_before_secret_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)

        def fail_recovery_construction(**_kwargs: object) -> None:
            raise TypeError("injected recovery construction failure")

        monkeypatch.setattr(
            atomic_file,
            "ConfigPreparationRecovery",
            fail_recovery_construction,
        )
        result = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-prep-recovery-constructor"),
        )
        assert _code(result) == "config.atomic_write_failed"
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())
        assert (home / "config.toml").read_bytes() == raw


@pytest.mark.skipif(os.name != "nt", reason="native Windows hard-link semantics")
def test_windows_preparation_cleanup_rejects_hardlinked_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        hardlink = home / "owned-test-hardlink"

        def hardlink_then_fail(prepared: Any) -> None:
            try:
                os.link(home / prepared.snapshot_reference, hardlink)
            except OSError as exc:
                pytest.fail(
                    "required native Windows hard-link fixture could not be created: "
                    f"{type(exc).__name__}"
                )
            raise atomic_file._error(
                "config.external_change",
                "Injected final readiness failure.",
            )

        monkeypatch.setattr(
            atomic_file.PreparedAtomicFile,
            "_require_ready",
            hardlink_then_fail,
        )
        result = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-windows-hardlink-cleanup"),
        )
        assert _code(result) == "config.commit_ambiguous"
        assert isinstance(result.error, atomic_file.ConfigPreparationError)
        assert hardlink.is_file()
        hardlink.unlink()
        atomic_file.cleanup_config_preparation(result.error).unwrap()
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


def test_mutated_transaction_reference_cannot_redirect_staging(tmp_path: Path) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        transaction = begin_config_transaction("tx-mutated-reference")
        object.__setattr__(
            transaction,
            "_candidate_reference",
            ".zagrosi-config-tx-attacker.candidate",
        )
        result = prepare_atomic_candidate(proof, snapshot, candidate, transaction)
        assert _code(result) == "config.external_change"
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


def test_commit_result_mutation_is_rejected_at_access_boundary(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.atomic_file import (
        ConfigCommitState,
        begin_config_transaction,
        prepare_atomic_candidate,
    )
    from zagrosi_forge.install.contracts import ForgeError

    with _config_context(tmp_path, None) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-mutated-result"),
        ).unwrap()
        with prepared:
            committed = _commit_acknowledged(prepared, expected=snapshot).unwrap()
            object.__setattr__(
                committed,
                "_state",
                ConfigCommitState.THIRD_PARTY,
            )
            with pytest.raises(ForgeError) as caught:
                _ = committed.state
            assert caught.value.code == "config.external_change"


def test_candidate_is_private_while_staged_and_preserves_supported_mode(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        cleanup_config_recovery,
        prepare_atomic_candidate,
        promote_config_backup,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw, mode=0o644) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-preserve-mode"),
        ).unwrap()
        with prepared:
            if os.name != "nt":
                assert (
                    stat.S_IMODE((home / prepared.candidate_reference).stat().st_mode)
                    == 0o600
                )
            committed = _commit_acknowledged(prepared, expected=snapshot).unwrap()
            assert promote_config_backup(committed).unwrap() is None
            cleanup_config_recovery(committed).unwrap()
        if os.name != "nt":
            assert stat.S_IMODE((home / "config.toml").stat().st_mode) == 0o644


def test_windows_supported_dacl_and_attributes_are_preserved_exactly(
    tmp_path: Path,
) -> None:
    if os.name != "nt":
        return

    import zagrosi_forge.install.paths as paths
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        cleanup_config_recovery,
        prepare_atomic_candidate,
        promote_config_backup,
    )
    from zagrosi_forge.install.config import _windows_metadata_projection

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-windows-metadata"),
        ).unwrap()
        with prepared:
            committed = _commit_acknowledged(prepared, expected=snapshot).unwrap()
            descriptor = paths._windows_open_child(
                prepared._parent_descriptor,
                "config.toml",
                directory=False,
                read_data=True,
            )
            try:
                attributes, authorization = _windows_metadata_projection(descriptor)
                assert attributes == snapshot._windows_attributes
                assert authorization == snapshot._windows_authorization
            finally:
                paths._windows_close(descriptor)
            assert promote_config_backup(committed).unwrap() is None
            cleanup_config_recovery(committed).unwrap()


def test_posix_supported_xattr_is_preserved_by_atomic_replace(tmp_path: Path) -> None:
    if os.name == "nt":
        return

    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        cleanup_config_recovery,
        prepare_atomic_candidate,
        promote_config_backup,
    )

    name = b"com.zagrosi.spike" if sys.platform == "darwin" else b"user.zagrosi.spike"
    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw, xattrs={name: b"preserve-me"}) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-preserve-xattr"),
        ).unwrap()
        with prepared:
            committed = _commit_acknowledged(prepared, expected=snapshot).unwrap()
            assert promote_config_backup(committed).unwrap() is None
            cleanup_config_recovery(committed).unwrap()
        assert _get_test_xattr(home / "config.toml", name) == b"preserve-me"


def test_backup_record_binds_original_identity_and_exclusive_path(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        cleanup_config_recovery,
        prepare_atomic_candidate,
        promote_config_backup,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source, persistent_backup=True)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-backup"),
        ).unwrap()
        with prepared:
            assert prepared.backup_record is None
            final_backup = home / begin_config_transaction("tx-backup").backup_reference
            assert not final_backup.exists()
            committed = _commit_acknowledged(prepared, expected=snapshot).unwrap()
            assert committed.backup_record is None
            assert _code(cleanup_config_recovery(committed)) == (
                "config.commit_ambiguous"
            )
            record = promote_config_backup(committed).unwrap()
            assert record is not None
            assert record.original_identity == snapshot.leaf_identity
            assert record.original_digest == snapshot.byte_digest
            assert (home / record.relative_path).read_bytes() == raw
            assert "CANARY" not in repr(record)
            cleanup_config_recovery(committed).unwrap()
            assert not (home / committed.snapshot_reference).exists()


def test_backup_promotion_is_retryable_after_rename_before_state_mark(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.atomic_file as atomic_file

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source, persistent_backup=True)
        prepared = atomic_file.prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            atomic_file.begin_config_transaction("tx-backup-rename-fault"),
        ).unwrap()
        with prepared:
            committed = _commit_acknowledged(
                prepared,
                expected=snapshot,
            ).unwrap()
            if os.name == "nt":
                original_rename = atomic_file._paths._windows_rename_handle

                def windows_rename_then_fail(
                    descriptor: int,
                    parent: int,
                    destination: str,
                ) -> None:
                    original_rename(descriptor, parent, destination)
                    raise OSError("injected post-rename failure")

                target = atomic_file._paths
                attribute = "_windows_rename_handle"
                hook = windows_rename_then_fail
            else:
                original_posix_rename = atomic_file._paths._exclusive_posix_rename

                def posix_rename_then_fail(
                    parent: int,
                    source_name: str,
                    destination: str,
                ) -> None:
                    original_posix_rename(parent, source_name, destination)
                    raise OSError("injected post-rename failure")

                target = atomic_file._paths
                attribute = "_exclusive_posix_rename"
                hook = posix_rename_then_fail
            with monkeypatch.context() as scoped:
                scoped.setattr(target, attribute, hook)
                assert (
                    _code(atomic_file.promote_config_backup(committed))
                    == "config.commit_ambiguous"
                )
            record = atomic_file.promote_config_backup(committed).unwrap()
            assert record is not None
            assert record.original_identity == snapshot.leaf_identity
            assert record.original_digest == snapshot.byte_digest
            assert (home / record.relative_path).read_bytes() == raw
            atomic_file.cleanup_config_recovery(committed).unwrap()


def test_persistent_backup_collision_or_quota_fails_before_commit(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source, persistent_backup=True)
        transaction = begin_config_transaction("tx-backup-collision")
        occupied = home / transaction.backup_reference
        occupied.write_bytes(b"third-party")
        if os.name != "nt":
            occupied.chmod(0o600)
        result = prepare_atomic_candidate(proof, snapshot, candidate, transaction)
        assert _code(result) == "config.backup_policy_exceeded"
        assert (home / "config.toml").read_bytes() == raw
        assert occupied.read_bytes() == b"third-party"


def test_persistent_backup_record_quota_fails_before_staging(tmp_path: Path) -> None:
    import zagrosi_forge.install.paths as paths
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )
    from zagrosi_forge.install.policies import RECOVERY_RETENTION_POLICY

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        home,
        _root,
        _authority,
    ):
        if os.name == "nt":
            parent = paths._windows_open_path(os.fspath(home))
            try:
                for index in range(RECOVERY_RETENTION_POLICY.backups["max_records"]):
                    descriptor = paths._windows_create_private_file(
                        parent,
                        f".zagrosi-config-backup-quota-{index}.toml",
                    )
                    try:
                        paths._windows_write(descriptor, b"backup")
                    finally:
                        paths._windows_close(descriptor)
            finally:
                paths._windows_close(parent)
        else:
            for index in range(RECOVERY_RETENTION_POLICY.backups["max_records"]):
                backup = home / f".zagrosi-config-backup-quota-{index}.toml"
                backup.write_bytes(b"backup")
                backup.chmod(0o600)
        candidate = _atomic_inputs(
            snapshot,
            identity,
            source,
            persistent_backup=True,
        )
        result = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-backup-quota"),
        )
        assert _code(result) == "config.backup_policy_exceeded"
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())
        assert (home / "config.toml").read_bytes() == raw


def test_unsupported_xattr_or_native_windows_dacl_stops_before_snapshot(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.config import snapshot_config
    from zagrosi_forge.install.paths import PlatformPathAuthority

    home = tmp_path / "codex-home"
    _private_directory(home)
    config = _write_private_test_file(
        home,
        "config.toml",
        b'secret_token = "CANARY-METADATA"\n',
    )
    clear_windows_dacl = False
    root = None
    proof = None
    try:
        if os.name != "nt":
            config.chmod(0o600)
            name = (
                "com.zagrosi.unhandled"
                if sys.platform == "darwin"
                else "user.zagrosi.unhandled"
            )
            setter = getattr(os, "setxattr", None)
            if callable(setter):
                try:
                    setter(config, name, b"unsupported")
                except OSError as exc:
                    pytest.fail(
                        f"required metadata fixture unavailable: {type(exc).__name__}"
                    )
            else:
                completed = subprocess.run(
                    [
                        "/usr/bin/xattr",
                        "-w",
                        name,
                        "unsupported",
                        os.fspath(config),
                    ],
                    check=False,
                    capture_output=True,
                    timeout=10,
                )
                if completed.returncode != 0:
                    pytest.fail("required metadata fixture unavailable: xattr")
        else:
            completed = subprocess.run(
                ["icacls.exe", os.fspath(config), "/grant", "*S-1-1-0:R"],
                check=False,
                capture_output=True,
                timeout=10,
            )
            if completed.returncode != 0:
                pytest.fail("required native Windows DACL fixture could not be created")
            clear_windows_dacl = True
        authority = PlatformPathAuthority()
        root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
        proof = authority.prove_config_path(root).unwrap()
        result = snapshot_config(proof)
        assert _code(result) == "config.unsupported_metadata"
        assert not any("zagrosi-config" in item.name for item in home.iterdir())
        assert "CANARY-METADATA" not in repr(result)
    finally:
        if clear_windows_dacl:
            if proof is not None:
                proof.close()
                proof = None
            restored = subprocess.run(
                ["icacls.exe", os.fspath(config), "/remove:g", "*S-1-1-0"],
                check=False,
                capture_output=True,
                timeout=10,
            )
            if restored.returncode != 0:
                pytest.fail("native Windows DACL fixture could not be restored")
        if proof is not None:
            proof.close()
        if root is not None:
            root.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="native macOS metadata")
@pytest.mark.parametrize("metadata_kind", ["extended-acl", "file-flags"])
def test_native_macos_acl_or_flags_stop_before_snapshot(
    tmp_path: Path,
    metadata_kind: str,
) -> None:
    from zagrosi_forge.install.config import snapshot_config
    from zagrosi_forge.install.paths import PlatformPathAuthority

    home = tmp_path / "codex-home"
    _private_directory(home)
    config = _write_private_test_file(home, "config.toml", b"safe = true\n")
    apply = (
        ["/bin/chmod", "+a", "everyone deny execute", os.fspath(config)]
        if metadata_kind == "extended-acl"
        else ["/usr/bin/chflags", "hidden", os.fspath(config)]
    )
    clear = (
        ["/bin/chmod", "-N", os.fspath(config)]
        if metadata_kind == "extended-acl"
        else ["/usr/bin/chflags", "nohidden", os.fspath(config)]
    )
    applied = subprocess.run(
        apply,
        check=False,
        capture_output=True,
        timeout=10,
    )
    if applied.returncode != 0:
        pytest.fail(f"required native macOS {metadata_kind} fixture unavailable")
    authority = PlatformPathAuthority()
    root = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    proof = authority.prove_config_path(root).unwrap()
    try:
        assert _code(snapshot_config(proof)) == "config.unsupported_metadata"
        assert not any("zagrosi-config" in item.name for item in home.iterdir())
    finally:
        subprocess.run(clear, check=True, capture_output=True, timeout=10)
        proof.close()
        root.close()


def test_metadata_capability_probe_failure_is_not_reported_as_parse_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.config as config_module

    with _config_context(tmp_path, b"safe = true\n") as (
        _snapshot,
        _identity_value,
        _source,
        proof,
        *_rest,
    ):
        target = (
            "_windows_metadata_projection" if os.name == "nt" else "_descriptor_xattrs"
        )
        monkeypatch.setattr(
            config_module,
            target,
            lambda *_args: (_ for _ in ()).throw(OSError("metadata probe")),
        )
        assert _code(config_module.snapshot_config(proof)) == (
            "config.unsupported_metadata"
        )


def test_posix_foreign_group_metadata_stops_before_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if os.name == "nt":
        return

    import zagrosi_forge.install.config as config_module

    with _config_context(tmp_path, b"safe = true\n") as (
        _snapshot,
        _identity_value,
        _source,
        proof,
        home,
        _root,
        _authority,
    ):
        monkeypatch.setattr(config_module.os, "getegid", lambda: os.getgid() + 1)
        result = config_module.snapshot_config(proof)
        assert _code(result) == "config.unsupported_metadata"
        assert not any("zagrosi-config-tx" in item.name for item in home.iterdir())


def test_private_snapshot_bytes_never_enter_export_or_diagnostics(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.atomic_file import (
        begin_config_transaction,
        prepare_atomic_candidate,
    )

    raw = _fixture("cases/preserve.toml")
    with _config_context(tmp_path, raw) as (
        snapshot,
        identity,
        source,
        proof,
        _home,
        _root,
        _authority,
    ):
        candidate = _atomic_inputs(snapshot, identity, source)
        prepared = prepare_atomic_candidate(
            proof,
            snapshot,
            candidate,
            begin_config_transaction("tx-private-snapshot"),
        ).unwrap()
        with prepared:
            assert "CANARY-DO-NOT-LEAK" not in repr(prepared)
            with pytest.raises(TypeError):
                prepared.__reduce__()
