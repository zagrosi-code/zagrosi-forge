from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
import sys
from typing import Mapping

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


def _write_private_file(directory: Path, name: str, raw: bytes) -> Path:
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


def _reference(raw: str):
    from zagrosi_forge.install.paths import validate_reference
    from zagrosi_forge.install.policies import LIMIT_POLICY

    return validate_reference(raw, role="test", limits=LIMIT_POLICY).unwrap()


def _identity():
    from zagrosi_forge.install.contracts import InstallIdentity

    return InstallIdentity(
        marketplace_id="zagrosi",
        plugin_id="zagrosi-forge",
        base_version="0.2.0",
        install_version=f"0.2.0+codex.local-{'c' * 32}",
        base_payload_digest="c" * 64,
        rendered_payload_digest="d" * 64,
        policy_digest="e" * 64,
        transformation_profile="plugin-v1",
        contract_versions=("bundle-v1", "config-v1"),
    )


def _prepared_receipt(
    identity,
    transaction_id: str,
    before: str,
    after: str,
    *,
    effective_id: str = "zagrosi",
):
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.ownership import RECEIPT_SCHEMA_DIGEST

    platform = (
        "windows"
        if os.name == "nt"
        else "macos"
        if sys.platform == "darwin"
        else "linux"
    )
    record = {
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
            "contract_versions": identity.contract_versions,
        },
        "transaction": {"id": transaction_id, "lineage": (transaction_id,)},
        "source": {
            "relative_path": (
                f"sources/{effective_id}/zagrosi-forge/"
                f"{identity.install_version}/marketplace"
            ),
            "manifest_digest": "8" * 64,
        },
        "cache": {
            "relative_path": (
                f"cache/{effective_id}/zagrosi-forge/{identity.install_version}"
            ),
            "manifest_digest": "7" * 64,
        },
        "config": {
            "path_id": "codex-config",
            "before_digest": before,
            "after_digest": after,
        },
        "tools": {
            "installer_version": "0.2.0",
            "python_version": "3.11.0",
            "codex_version": "0.144.4",
            "platform": platform,
            "verifier_version": "1.0.0",
        },
        "created_at": "2026-07-17T00:00:00Z",
    }
    record["record_digest"] = hashlib.sha256(canonical_json_bytes(record)).hexdigest()
    return record


def _config_recovery_descriptor(transaction_id: str):
    from zagrosi_forge.install.atomic_file import decode_config_recovery_descriptor
    from zagrosi_forge.install.config import CONFIG_METADATA_POLICY
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.policies import LIMIT_POLICY, RECOVERY_RETENTION_POLICY

    transaction_digest = hashlib.sha256(transaction_id.encode("utf-8")).hexdigest()
    tag = transaction_digest[:24]
    if os.name == "nt":
        target_posix_mode = None
        target_windows_attributes = 0x20
        target_windows_authorization = (
            "owner",
            "group",
            "P",
            (("A", "", "FA", "", "", "owner"),),
        )
    else:
        target_posix_mode = 0o600
        target_windows_attributes = None
        target_windows_authorization = None
    target_metadata = {
        "platform": "windows" if os.name == "nt" else "posix",
        "posix_mode": target_posix_mode,
        "posix_xattrs": (),
        "windows_attributes": target_windows_attributes,
        "windows_authorization": target_windows_authorization,
    }
    domain = {
        "backup_stage_metadata_digest": None,
        "backup_identity": None,
        "backup_reference": f".zagrosi-config-backup-{tag}.toml",
        "backup_stage_reference": f".zagrosi-config-tx-{tag}.backup",
        "before_byte_digest": "1" * 64,
        "before_identity": (11, 13),
        "before_mode": 0o600 if os.name != "nt" else None,
        "before_snapshot_digest": "4" * 64,
        "candidate_byte_digest": "5" * 64,
        "candidate_identity": (11, 15),
        "candidate_reference": f".zagrosi-config-tx-{tag}.candidate",
        "candidate_stage_metadata_digest": "a" * 64,
        "descriptor_version": "1.0",
        "displaced_identity": None,
        "displaced_reference": (
            f".zagrosi-config-tx-{tag}.displaced"
            if os.name == "nt"
            else f".zagrosi-config-tx-{tag}.candidate"
        ),
        "limit_policy_version": LIMIT_POLICY.version,
        "metadata_fingerprint": "3" * 64,
        "metadata_policy_version": CONFIG_METADATA_POLICY.version,
        "parent_identity": (11, 12),
        "persistent_backup": False,
        "platform": "windows" if os.name == "nt" else "posix",
        "retention_policy_version": RECOVERY_RETENTION_POLICY.version,
        "snapshot_identity": (11, 14),
        "snapshot_reference": f".zagrosi-config-tx-{tag}.snapshot",
        "snapshot_stage_metadata_digest": "b" * 64,
        "target_metadata_digest": hashlib.sha256(
            canonical_json_bytes(target_metadata)
        ).hexdigest(),
        "target_posix_mode": target_posix_mode,
        "target_posix_xattrs": (),
        "target_windows_attributes": target_windows_attributes,
        "target_windows_authorization": target_windows_authorization,
        "transaction_digest": transaction_digest,
    }
    record = {
        **domain,
        "descriptor_digest": hashlib.sha256(canonical_json_bytes(domain)).hexdigest(),
    }
    return decode_config_recovery_descriptor(record).unwrap()


def _mutated_config_recovery_descriptor(descriptor, field: str, value: object):
    from zagrosi_forge.install.atomic_file import decode_config_recovery_descriptor
    from zagrosi_forge.install.contracts import canonical_json_bytes

    record = dict(descriptor.to_record())
    record[field] = value
    domain = {key: item for key, item in record.items() if key != "descriptor_digest"}
    record["descriptor_digest"] = hashlib.sha256(
        canonical_json_bytes(domain)
    ).hexdigest()
    return decode_config_recovery_descriptor(record).unwrap()


def _prepared(binding, *, effective_id: str = "zagrosi"):
    from zagrosi_forge.install.journal import (
        JournalConfigIdentity,
        JournalPathIdentity,
        PreparedTransaction,
        RollbackAction,
        TransactionOwnedPath,
    )

    transaction_id = binding.transaction_id
    descriptor = _config_recovery_descriptor(transaction_id)
    before = JournalConfigIdentity(
        parent_identity=(11, 12),
        leaf_identity=(11, 13),
        byte_digest="1" * 64,
        semantic_digest="2" * 64,
        metadata_fingerprint="3" * 64,
        snapshot_digest="4" * 64,
        target_metadata_digest=None,
    )
    candidate = JournalConfigIdentity(
        parent_identity=(11, 12),
        leaf_identity=None,
        byte_digest="5" * 64,
        semantic_digest="6" * 64,
        metadata_fingerprint="3" * 64,
        snapshot_digest="4" * 64,
        target_metadata_digest=descriptor.target_metadata_digest,
    )
    transaction_root = binding.root_relative
    paths = (
        JournalPathIdentity(
            role="transaction-root",
            relative_path=transaction_root,
            parent_identity=binding.store_identity,
            leaf_identity=binding.transaction_identity,
            content_digest=None,
        ),
        JournalPathIdentity(
            role="source-generation",
            relative_path=(
                f"sources/{effective_id}/zagrosi-forge/"
                f"0.2.0+codex.local-{'c' * 32}/marketplace"
            ),
            parent_identity=(20, 23),
            leaf_identity=None,
            content_digest="8" * 64,
        ),
        JournalPathIdentity(
            role="cache-generation",
            relative_path=(
                f"cache/{effective_id}/zagrosi-forge/0.2.0+codex.local-{'c' * 32}"
            ),
            parent_identity=(20, 24),
            leaf_identity=None,
            content_digest="7" * 64,
        ),
    )
    owned = (
        TransactionOwnedPath(
            role="transaction-root",
            relative_path=transaction_root,
            expected_identity=binding.transaction_identity,
        ),
        TransactionOwnedPath(
            role="config-snapshot",
            relative_path=f"{transaction_root}/config.snapshot",
            expected_identity=None,
        ),
    )
    rollback = (
        RollbackAction(
            action="quarantine-if-owned",
            relative_path=transaction_root,
            expected_identity=binding.transaction_identity,
        ),
    )
    identity = _identity()
    return PreparedTransaction(
        transaction_id=transaction_id,
        effective_marketplace_id=effective_id,
        config_transaction_digest=hashlib.sha256(
            transaction_id.encode("utf-8")
        ).hexdigest(),
        plan_digest="9" * 64,
        runner_provenance=_runner(),
        install_identity=identity,
        before_relation_digest="a" * 64,
        candidate_relation_digest="b" * 64,
        before_config=before,
        candidate_config=candidate,
        identities=paths,
        transaction_owned_paths=owned,
        rollback_actions=rollback,
        prepared_receipt=_prepared_receipt(
            identity,
            transaction_id,
            before.byte_digest,
            candidate.byte_digest,
            effective_id=effective_id,
        ),
        verification_evidence_digest=None,
    )


def _source_result(prepared, *, identity=(20, 31)):
    return replace(
        next(item for item in prepared.identities if item.role == "source-generation"),
        leaf_identity=identity,
    )


def _cache_result(prepared, *, identity=(20, 32)):
    return replace(
        next(item for item in prepared.identities if item.role == "cache-generation"),
        leaf_identity=identity,
    )


def _config_result(prepared, descriptor):
    return replace(
        prepared.candidate_config,
        leaf_identity=descriptor.candidate_identity,
        metadata_fingerprint="9" * 64,
        snapshot_digest="a" * 64,
    )


def _receipt_result(prepared, *, identity=(20, 33)):
    from zagrosi_forge.install.contracts import canonical_json_bytes
    from zagrosi_forge.install.journal import JournalPathIdentity
    from zagrosi_forge.install.ownership import committed_receipt_reference

    return JournalPathIdentity(
        role="committed-receipt",
        relative_path=committed_receipt_reference(
            prepared.effective_marketplace_id,
            prepared.install_identity,
        ).value,
        parent_identity=(20, 25),
        leaf_identity=identity,
        content_digest=hashlib.sha256(
            canonical_json_bytes(prepared.prepared_receipt, final_newline=True)
        ).hexdigest(),
    )


def _advance_to_commit_intent(store, prepared, descriptor):
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    head = store.create_prepared(prepared)
    for transition in (
        JournalTransition(JournalState.STAGED, config_recovery=descriptor),
        JournalTransition(
            JournalState.VERIFIED,
            verification_evidence_digest="f" * 64,
        ),
        JournalTransition(
            JournalState.SOURCE_PUBLISHED,
            source_result=_source_result(prepared),
        ),
        JournalTransition(
            JournalState.CACHE_PUBLISHED,
            cache_result=_cache_result(prepared),
        ),
        JournalTransition(JournalState.PUBLISHED),
        JournalTransition(JournalState.COMMIT_INTENT),
    ):
        head = store.append(head, transition)
    return head


def _advance_to_finalized(store, prepared, descriptor):
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    head = _advance_to_commit_intent(store, prepared, descriptor)
    for transition in (
        JournalTransition(
            JournalState.CONFIG_COMMITTED,
            config_result=_config_result(prepared, descriptor),
        ),
        JournalTransition(
            JournalState.RECEIPT_COMMITTED,
            receipt_result=_receipt_result(prepared),
        ),
        JournalTransition(JournalState.FINALIZED),
    ):
        head = store.append(head, transition)
    return head


def _store(
    tmp_path: Path,
    *,
    transaction_id: str = "tx-0123456789abcdef0123456789abcdef",
    authority=None,
):
    from zagrosi_forge.install.journal import JournalStore
    from zagrosi_forge.install.ownership import (
        create_persistent_transaction_root,
        open_transaction_journal_access,
    )
    from zagrosi_forge.install.paths import PlatformPathAuthority

    home = tmp_path / "codex-home"
    _private_directory(home)
    authority = PlatformPathAuthority() if authority is None else authority
    owned = authority.bootstrap_forge_root(home, runner=_runner()).unwrap()
    created = create_persistent_transaction_root(
        owned,
        transaction_id=transaction_id,
    ).unwrap()
    proof = authority.prove_descendant(
        owned,
        created.claim.relative,
        expected_depth=3,
    ).unwrap()
    directory = home / "plugins" / created.binding.root_relative
    access = open_transaction_journal_access(owned, created).unwrap()
    return JournalStore(access, proof), proof, owned, directory, created.binding


def _bind_prepared_to_current_config(prepared, current):
    candidate = replace(
        prepared.candidate_config,
        parent_identity=current.parent_identity,
        leaf_identity=None,
        metadata_fingerprint=current.metadata_fingerprint,
        snapshot_digest=current.snapshot_digest,
    )
    receipt = _prepared_receipt(
        prepared.install_identity,
        prepared.transaction_id,
        current.byte_digest,
        candidate.byte_digest,
        effective_id=prepared.effective_marketplace_id,
    )
    return replace(
        prepared,
        before_config=current,
        candidate_config=candidate,
        prepared_receipt=receipt,
    )


def _record_bytes(record: Mapping[str, object]) -> bytes:
    value = dict(record)
    value.pop("record_digest", None)
    digest_input = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    value["record_digest"] = hashlib.sha256(digest_input).hexdigest()
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _rewrite(path: Path, transform) -> None:
    decoded = json.loads(path.read_bytes())
    path.write_bytes(_record_bytes(transform(decoded)))


def _error_code(raised: pytest.ExceptionInfo[BaseException]) -> str:
    from zagrosi_forge.install.contracts import ForgeError

    assert isinstance(raised.value, ForgeError)
    assert raised.value.exit_category == 14
    return raised.value.code


def test_journal_is_durable_before_any_publish(tmp_path: Path) -> None:
    from zagrosi_forge.install.journal import JournalState

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        head = store.create_prepared(_prepared(binding))
        path = directory / "journal-00000000.json"
        before = (path.stat().st_dev, path.stat().st_ino, path.read_bytes())
        assert head.sequence == 0
        assert head.state is JournalState.PREPARED
        assert path.read_bytes().endswith(b"\n")

        loaded = store.load()
        assert loaded.head == head
        assert loaded.records[0].sequence == 0
        assert loaded.records[0].previous_record_digest == "0" * 64
        assert (path.stat().st_dev, path.stat().st_ino, path.read_bytes()) == before
    finally:
        store.close()
        proof.close()
        owned.close()


def test_journal_reopens_only_through_exact_persistent_transaction_binding(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import JournalStore
    from zagrosi_forge.install.ownership import (
        load_persistent_transaction_binding,
        rebind_persistent_transaction,
    )
    from zagrosi_forge.install.paths import PlatformPathAuthority

    store, proof, owned, _directory, binding = _store(tmp_path)
    head = store.create_prepared(_prepared(binding))
    store.close()
    proof.close()
    owned.close()

    authority = PlatformPathAuthority()
    reopened_root = authority.bootstrap_forge_root(
        tmp_path / "codex-home", runner=_runner()
    ).unwrap()
    rebound = None
    reopened_proof = None
    reopened_access = None
    reopened_store = None
    try:
        loaded_binding = load_persistent_transaction_binding(
            reopened_root,
            transaction_id=binding.transaction_id,
        ).unwrap()
        rebound = rebind_persistent_transaction(
            reopened_root,
            binding=loaded_binding,
        ).unwrap()
        assert rebound.claim is not None
        reopened_proof = authority.prove_descendant(
            reopened_root,
            rebound.claim.relative,
            expected_depth=3,
        ).unwrap()
        from zagrosi_forge.install.ownership import open_transaction_journal_access

        reopened_access = open_transaction_journal_access(
            reopened_root,
            rebound,
        ).unwrap()
        reopened_store = JournalStore(reopened_access, reopened_proof)
        reopened_access = None
        assert reopened_store.load().head == head
    finally:
        if reopened_store is not None:
            reopened_store.close()
        elif reopened_access is not None:
            reopened_access.close()
        if reopened_proof is not None:
            reopened_proof.close()
        if rebound is not None:
            rebound.close()
        reopened_root.close()


@pytest.mark.parametrize("tamper", ("unlink", "replace", "hardlink"))
def test_journal_store_revalidates_sibling_anchor_before_load(
    tmp_path: Path,
    tamper: str,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalStore
    from zagrosi_forge.install.ownership import (
        open_transaction_journal_access,
        rebind_persistent_transaction,
    )

    store, proof, owned, directory, binding = _store(tmp_path)
    rebound = None
    access = None
    secured_store = None
    try:
        head = store.create_prepared(_prepared(binding))
        before = tuple(
            (path.name, path.read_bytes())
            for path in sorted(directory.glob("journal*"))
        )
        store.close()
        rebound = rebind_persistent_transaction(owned, binding=binding).unwrap()
        access = open_transaction_journal_access(owned, rebound).unwrap()
        secured_store = JournalStore(access, proof)
        access = None

        anchor = tmp_path / "codex-home" / "plugins" / binding.claim_relative
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
                pytest.skip(f"hard links unavailable: {exc}")

        with pytest.raises(ForgeError) as raised:
            secured_store.load()
        assert raised.value.code == "journal.corrupt"
        assert (
            tuple(
                (path.name, path.read_bytes())
                for path in sorted(directory.glob("journal*"))
            )
            == before
        )
        assert head.sequence == 0
    finally:
        if secured_store is not None:
            secured_store.close()
        elif access is not None:
            access.close()
        if rebound is not None:
            rebound.close()
        store.close()
        proof.close()
        owned.close()


def test_quarantined_journal_reopens_read_only_without_mutation(tmp_path: Path) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import (
        JournalState,
        JournalStore,
        JournalTransition,
        load_pending,
    )
    from zagrosi_forge.install.ownership import (
        open_transaction_journal_access,
        prove_transaction_owned,
        quarantine_owned,
        rebind_persistent_transaction,
    )

    store, proof, owned, _directory, binding = _store(tmp_path)
    live_rebound = None
    quarantine_rebound = None
    quarantine_ticket = None
    access = None
    reopened_store = None
    try:
        head = store.create_prepared(_prepared(binding))
        store.close()

        live_rebound = rebind_persistent_transaction(owned, binding=binding).unwrap()
        assert live_rebound.claim is not None
        cleanup_proof = prove_transaction_owned(
            proof,
            claim=live_rebound.claim,
        ).unwrap()
        quarantine_ticket = quarantine_owned(
            cleanup_proof,
            transaction_id=binding.transaction_id,
        ).unwrap()

        quarantine_rebound = rebind_persistent_transaction(
            owned,
            binding=binding,
        ).unwrap()
        access = open_transaction_journal_access(
            owned,
            quarantine_rebound,
        ).unwrap()
        reopened_store = JournalStore(access)
        access = None
        quarantined_directory = (
            tmp_path / "codex-home" / "plugins" / binding.quarantine_relative
        )
        before = tuple(
            (path.name, path.read_bytes())
            for path in sorted(quarantined_directory.glob("journal*"))
        )

        assert reopened_store.load().head == head
        with pytest.raises(ForgeError) as create_error:
            reopened_store.create_prepared(_prepared(binding))
        assert create_error.value.code == "ownership.unowned"
        with pytest.raises(ForgeError) as append_error:
            reopened_store.append(
                head,
                JournalTransition(state=JournalState.STAGED),
            )
        assert append_error.value.code == "ownership.unowned"
        assert (
            tuple(
                (path.name, path.read_bytes())
                for path in sorted(quarantined_directory.glob("journal*"))
            )
            == before
        )
        reopened_store.close()
        reopened_store = None
        loaded = load_pending(owned)
        assert len(loaded) == 1
        assert loaded[0].head == head
    finally:
        if reopened_store is not None:
            reopened_store.close()
        elif access is not None:
            access.close()
        if quarantine_rebound is not None:
            quarantine_rebound.close()
        if live_rebound is not None:
            live_rebound.close()
        if quarantine_ticket is not None:
            quarantine_ticket.close()
        store.close()
        proof.close()
        owned.close()


def test_prepared_record_must_match_sealed_transaction_binding(tmp_path: Path) -> None:
    from zagrosi_forge.install.contracts import ForgeError

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        root_identity = prepared.identities[-1]
        changed = (root_identity.leaf_identity[0], root_identity.leaf_identity[1] + 1)
        identities = prepared.identities[:-1] + (
            replace(root_identity, leaf_identity=changed),
        )
        owned_paths = tuple(
            replace(item, expected_identity=changed)
            if item.role == "transaction-root"
            else item
            for item in prepared.transaction_owned_paths
        )
        rollback_actions = tuple(
            replace(item, expected_identity=changed)
            if item.relative_path == binding.root_relative
            else item
            for item in prepared.rollback_actions
        )
        mismatched = replace(
            prepared,
            identities=identities,
            transaction_owned_paths=owned_paths,
            rollback_actions=rollback_actions,
        )
        with pytest.raises(ForgeError) as raised:
            store.create_prepared(mismatched)
        assert raised.value.code == "journal.corrupt"
        assert not tuple(directory.glob("journal*"))
    finally:
        store.close()
        proof.close()
        owned.close()


def test_journal_record_has_closed_complete_v1_projection(tmp_path: Path) -> None:
    from jsonschema import Draft202012Validator

    from zagrosi_forge.install.journal import JOURNAL_SCHEMA_DIGEST

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        store.create_prepared(_prepared(binding))
        record = json.loads((directory / "journal-00000000.json").read_bytes())
        assert set(record) == {
            "before_config",
            "before_relation_digest",
            "candidate_config",
            "candidate_relation_digest",
            "committed_config",
            "config_recovery",
            "config_transaction_digest",
            "effective_marketplace_id",
            "identities",
            "install_identity",
            "limit_policy_version",
            "minimum_reader_version",
            "plan_digest",
            "policy_version",
            "prepared_receipt",
            "previous_record_digest",
            "record_digest",
            "rollback_actions",
            "runner_provenance",
            "schema_digest",
            "schema_version",
            "sequence",
            "state",
            "state_machine_version",
            "transaction_id",
            "transaction_binding",
            "transaction_owned_paths",
            "verification_evidence_digest",
            "writer_version",
        }
        assert record["schema_version"] == "1.0"
        assert record["schema_digest"] == JOURNAL_SCHEMA_DIGEST
        assert record["minimum_reader_version"] == "0.2.0"
        assert record["state_machine_version"] == "1.0"
        assert record["policy_version"] == "1.0"
        assert record["limit_policy_version"] == "1.0"
        assert record["transaction_owned_paths"]
        assert record["identities"]
        assert record["prepared_receipt"]
        schema_path = (
            Path(__file__).parents[2]
            / "src/zagrosi_forge/install/schemas/transaction-journal-v1.schema.json"
        )
        schema_raw = schema_path.read_bytes()
        assert hashlib.sha256(schema_raw).hexdigest() == JOURNAL_SCHEMA_DIGEST
        schema = json.loads(schema_raw)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(record)
    finally:
        store.close()
        proof.close()
        owned.close()


def test_journal_chain_is_immutable_and_accepts_only_exact_forward_states(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = store.create_prepared(prepared)
        first = directory / "journal-00000000.json"
        first_before = (first.stat().st_ino, first.read_bytes())
        with pytest.raises(ForgeError) as skipped:
            store.append(head, JournalTransition(JournalState.VERIFIED))
        assert skipped.value.code == "journal.corrupt"
        assert not (directory / "journal-00000001.json").exists()

        states = (
            JournalState.STAGED,
            JournalState.VERIFIED,
            JournalState.SOURCE_PUBLISHED,
            JournalState.CACHE_PUBLISHED,
            JournalState.PUBLISHED,
            JournalState.COMMIT_INTENT,
            JournalState.CONFIG_COMMITTED,
            JournalState.RECEIPT_COMMITTED,
            JournalState.FINALIZED,
        )
        source_result = _source_result(prepared)
        cache_result = _cache_result(prepared)
        config_result = _config_result(prepared, descriptor)
        receipt_result = _receipt_result(prepared)
        previous = head.record_digest
        for state in states:
            transition = JournalTransition(
                state,
                config_recovery=(descriptor if state is JournalState.STAGED else None),
                verification_evidence_digest=(
                    "f" * 64 if state is JournalState.VERIFIED else None
                ),
                source_result=(
                    source_result if state is JournalState.SOURCE_PUBLISHED else None
                ),
                cache_result=(
                    cache_result if state is JournalState.CACHE_PUBLISHED else None
                ),
                config_result=(
                    config_result if state is JournalState.CONFIG_COMMITTED else None
                ),
                receipt_result=(
                    receipt_result if state is JournalState.RECEIPT_COMMITTED else None
                ),
            )
            head = store.append(head, transition)
            assert head.state is state
            record = json.loads(
                (directory / f"journal-{head.sequence:08d}.json").read_bytes()
            )
            assert record["previous_record_digest"] == previous
            previous = head.record_digest

        with pytest.raises(ForgeError) as terminal:
            store.append(head, JournalTransition(JournalState.RECOVERY_REQUIRED))
        assert terminal.value.code == "journal.corrupt"
        assert (first.stat().st_ino, first.read_bytes()) == first_before
        assert store.load().head == head
    finally:
        store.close()
        proof.close()
        owned.close()


def test_publication_results_are_required_at_their_exact_states(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = store.create_prepared(prepared)
        head = store.append(
            head,
            JournalTransition(JournalState.STAGED, config_recovery=descriptor),
        )
        head = store.append(
            head,
            JournalTransition(
                JournalState.VERIFIED,
                verification_evidence_digest="f" * 64,
            ),
        )

        with pytest.raises(ForgeError) as missing_source:
            store.append(head, JournalTransition(JournalState.SOURCE_PUBLISHED))
        assert missing_source.value.code == "journal.corrupt"
        with pytest.raises(ForgeError) as wrong_source:
            store.append(
                head,
                JournalTransition(
                    JournalState.SOURCE_PUBLISHED,
                    cache_result=_cache_result(prepared),
                ),
            )
        assert wrong_source.value.code == "journal.corrupt"
        head = store.append(
            head,
            JournalTransition(
                JournalState.SOURCE_PUBLISHED,
                source_result=_source_result(prepared),
            ),
        )

        with pytest.raises(ForgeError) as missing_cache:
            store.append(head, JournalTransition(JournalState.CACHE_PUBLISHED))
        assert missing_cache.value.code == "journal.corrupt"
        head = store.append(
            head,
            JournalTransition(
                JournalState.CACHE_PUBLISHED,
                cache_result=_cache_result(prepared),
            ),
        )
        head = store.append(head, JournalTransition(JournalState.PUBLISHED))
        head = store.append(head, JournalTransition(JournalState.COMMIT_INTENT))

        with pytest.raises(ForgeError) as missing_config:
            store.append(head, JournalTransition(JournalState.CONFIG_COMMITTED))
        assert missing_config.value.code == "journal.corrupt"
        head = store.append(
            head,
            JournalTransition(
                JournalState.CONFIG_COMMITTED,
                config_result=_config_result(prepared, descriptor),
            ),
        )

        with pytest.raises(ForgeError) as missing_receipt:
            store.append(head, JournalTransition(JournalState.RECEIPT_COMMITTED))
        assert missing_receipt.value.code == "journal.corrupt"
        head = store.append(
            head,
            JournalTransition(
                JournalState.RECEIPT_COMMITTED,
                receipt_result=_receipt_result(prepared),
            ),
        )
        assert (
            store.append(
                head,
                JournalTransition(JournalState.FINALIZED),
            ).state
            is JournalState.FINALIZED
        )
        assert len(tuple(directory.glob("journal-*.json"))) == 10
    finally:
        store.close()
        proof.close()
        owned.close()


def test_committed_config_preserves_planned_basis_and_records_observed_snapshot(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = _advance_to_commit_intent(store, prepared, descriptor)
        result = _config_result(prepared, descriptor)
        head = store.append(
            head,
            JournalTransition(
                JournalState.CONFIG_COMMITTED,
                config_result=result,
            ),
        )

        record = json.loads(
            (directory / f"journal-{head.sequence:08d}.json").read_bytes()
        )
        assert record["candidate_config"]["leaf_identity"] is None
        assert (
            record["candidate_config"]["metadata_fingerprint"]
            == prepared.candidate_config.metadata_fingerprint
        )
        assert (
            record["candidate_config"]["snapshot_digest"]
            == prepared.candidate_config.snapshot_digest
        )
        assert record["committed_config"]["leaf_identity"] == list(
            descriptor.candidate_identity
        )
        assert (
            record["committed_config"]["metadata_fingerprint"]
            == result.metadata_fingerprint
        )
        assert record["committed_config"]["snapshot_digest"] == result.snapshot_digest
        assert store.load().records[-1].committed_config == result
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("parent_identity", (11, 99)),
        ("leaf_identity", (11, 99)),
        ("byte_digest", "e" * 64),
        ("semantic_digest", "e" * 64),
        ("target_metadata_digest", "e" * 64),
    ),
)
def test_committed_config_binds_expected_publication_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = _advance_to_commit_intent(store, prepared, descriptor)
        result = replace(_config_result(prepared, descriptor), **{field: value})
        with pytest.raises(ForgeError) as raised:
            store.append(
                head,
                JournalTransition(
                    JournalState.CONFIG_COMMITTED,
                    config_result=result,
                ),
            )
        assert raised.value.code == "journal.corrupt"
        assert not (directory / "journal-00000007.json").exists()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_reload_rejects_missing_state_specific_publication_evidence(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = store.create_prepared(prepared)
        head = store.append(
            head,
            JournalTransition(JournalState.STAGED, config_recovery=descriptor),
        )
        head = store.append(
            head,
            JournalTransition(
                JournalState.VERIFIED,
                verification_evidence_digest="f" * 64,
            ),
        )
        store.append(
            head,
            JournalTransition(
                JournalState.SOURCE_PUBLISHED,
                source_result=_source_result(prepared),
            ),
        )
        published = directory / "journal-00000003.json"

        def remove_source_identity(value):
            identities = list(value["identities"])
            for identity in identities:
                if identity["role"] == "source-generation":
                    identity["leaf_identity"] = None
            return {**value, "identities": identities}

        _rewrite(published, remove_source_identity)
        retained = published.read_bytes()
        with pytest.raises(ForgeError) as raised:
            store.load()
        assert raised.value.code == "journal.corrupt"
        assert published.read_bytes() == retained
    finally:
        store.close()
        proof.close()
        owned.close()


def test_alternate_effective_marketplace_id_roundtrips_without_rewriting_identity(
    tmp_path: Path,
) -> None:
    effective_id = "zagrosi-local-" + "f" * 24
    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding, effective_id=effective_id)
        assert prepared.install_identity.marketplace_id == "zagrosi"
        assert prepared.effective_marketplace_id == effective_id
        store.create_prepared(prepared)
        record = json.loads((directory / "journal-00000000.json").read_bytes())
        assert record["effective_marketplace_id"] == effective_id
        assert record["install_identity"]["marketplace_id"] == "zagrosi"
        receipt = record["prepared_receipt"]
        assert receipt["effective_marketplace_id"] == effective_id
        assert receipt["source"]["relative_path"].startswith(f"sources/{effective_id}/")
        assert receipt["cache"]["relative_path"].startswith(f"cache/{effective_id}/")
        assert (
            store.load().records[0].record["effective_marketplace_id"] == effective_id
        )
    finally:
        store.close()
        proof.close()
        owned.close()


def test_effective_marketplace_and_generation_paths_are_receipt_bound(
    tmp_path: Path,
) -> None:
    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        with pytest.raises(ValueError, match="prepared receipt"):
            replace(
                prepared,
                effective_marketplace_id="zagrosi-local-" + "e" * 24,
            )
        for role in ("source-generation", "cache-generation"):
            identities = tuple(
                replace(item, content_digest="6" * 64) if item.role == role else item
                for item in prepared.identities
            )
            with pytest.raises(ValueError, match="prepared receipt"):
                replace(prepared, identities=identities)
        source = next(
            item for item in prepared.identities if item.role == "source-generation"
        )
        with pytest.raises(ValueError, match="prepared receipt"):
            replace(
                prepared,
                identities=prepared.identities
                + (replace(source, relative_path=source.relative_path + "-extra"),),
            )
    finally:
        store.close()
        proof.close()
        owned.close()


def test_verification_evidence_is_introduced_only_by_verified(tmp_path: Path) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = store.create_prepared(prepared)
        for state in (JournalState.STAGED, JournalState.RECOVERY_REQUIRED):
            with pytest.raises(ForgeError) as raised:
                store.append(
                    head,
                    JournalTransition(
                        state,
                        verification_evidence_digest="f" * 64,
                    ),
                )
            assert raised.value.code == "journal.corrupt"
            assert not (directory / "journal-00000001.json").exists()

        head = store.append(
            head,
            JournalTransition(JournalState.STAGED, config_recovery=descriptor),
        )
        head = store.append(
            head,
            JournalTransition(
                JournalState.VERIFIED,
                verification_evidence_digest="f" * 64,
            ),
        )
        with pytest.raises(ForgeError) as changed:
            store.append(
                head,
                JournalTransition(
                    JournalState.SOURCE_PUBLISHED,
                    verification_evidence_digest="e" * 64,
                ),
            )
        assert changed.value.code == "journal.corrupt"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_config_recovery_is_introduced_at_staged_and_roundtrips_inertly(
    tmp_path: Path,
) -> None:
    from jsonschema import Draft202012Validator

    from zagrosi_forge.install.atomic_file import decode_config_recovery_descriptor
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = store.create_prepared(prepared)
        with pytest.raises(ForgeError) as missing:
            store.append(head, JournalTransition(JournalState.STAGED))
        assert missing.value.code == "journal.corrupt"
        with pytest.raises(ForgeError) as early:
            store.append(
                head,
                JournalTransition(
                    JournalState.RECOVERY_REQUIRED,
                    config_recovery=descriptor,
                ),
            )
        assert early.value.code == "journal.corrupt"
        assert not (directory / "journal-00000001.json").exists()

        head = store.append(
            head,
            JournalTransition(JournalState.STAGED, config_recovery=descriptor),
        )
        staged = store.load().records[-1]
        assert staged.config_recovery == descriptor.to_record()
        schema_path = (
            Path(__file__).parents[2]
            / "src/zagrosi_forge/install/schemas/transaction-journal-v1.schema.json"
        )
        Draft202012Validator(json.loads(schema_path.read_bytes())).validate(
            json.loads((directory / "journal-00000001.json").read_bytes())
        )
        decoded = decode_config_recovery_descriptor(
            dict(staged.config_recovery)
        ).unwrap()
        assert decoded.to_record() == descriptor.to_record()
        head = store.append(
            head,
            JournalTransition(
                JournalState.VERIFIED,
                verification_evidence_digest="f" * 64,
            ),
        )
        assert store.load().records[-1].config_recovery == staged.config_recovery
        with pytest.raises(ForgeError) as late:
            store.append(
                head,
                JournalTransition(
                    JournalState.SOURCE_PUBLISHED,
                    config_recovery=descriptor,
                ),
            )
        assert late.value.code == "journal.corrupt"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_config_recovery_wrong_transaction_or_tamper_preserves_chain(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        head = store.create_prepared(prepared)
        wrong = _config_recovery_descriptor("tx-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        with pytest.raises(ForgeError) as mismatched:
            store.append(
                head,
                JournalTransition(JournalState.STAGED, config_recovery=wrong),
            )
        assert mismatched.value.code == "journal.corrupt"

        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        store.append(
            head,
            JournalTransition(JournalState.STAGED, config_recovery=descriptor),
        )
        staged = directory / "journal-00000001.json"

        def tamper(value):
            recovery = dict(value["config_recovery"])
            recovery["descriptor_digest"] = "0" * 64
            return {**value, "config_recovery": recovery}

        _rewrite(staged, tamper)
        retained = staged.read_bytes()
        with pytest.raises(ForgeError) as corrupted:
            store.load()
        assert corrupted.value.code == "journal.corrupt"
        assert staged.read_bytes() == retained
    finally:
        store.close()
        proof.close()
        owned.close()


def test_reload_rejects_self_consistent_recovery_descriptor_that_conflicts_with_prepared(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError, canonical_json_bytes
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = store.create_prepared(prepared)
        store.append(
            head,
            JournalTransition(JournalState.STAGED, config_recovery=descriptor),
        )
        staged = directory / "journal-00000001.json"

        def tamper(value):
            recovery = dict(value["config_recovery"])
            recovery["before_byte_digest"] = "e" * 64
            domain = {
                key: item
                for key, item in recovery.items()
                if key != "descriptor_digest"
            }
            recovery["descriptor_digest"] = hashlib.sha256(
                canonical_json_bytes(domain)
            ).hexdigest()
            return {**value, "config_recovery": recovery}

        _rewrite(staged, tamper)
        retained = staged.read_bytes()
        with pytest.raises(ForgeError) as corrupted:
            store.load()
        assert corrupted.value.code == "journal.corrupt"
        assert staged.read_bytes() == retained
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("before_byte_digest", "e" * 64),
        ("before_identity", (11, 99)),
        ("before_snapshot_digest", "e" * 64),
        ("candidate_byte_digest", "e" * 64),
        ("metadata_fingerprint", "e" * 64),
        ("parent_identity", (11, 99)),
    ),
)
def test_staged_recovery_descriptor_must_match_prepared_config_identity(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _mutated_config_recovery_descriptor(
            _config_recovery_descriptor(prepared.transaction_id),
            field,
            value,
        )
        head = store.create_prepared(prepared)
        with pytest.raises(ForgeError) as raised:
            store.append(
                head,
                JournalTransition(JournalState.STAGED, config_recovery=descriptor),
            )
        assert raised.value.code == "journal.corrupt"
        assert not (directory / "journal-00000001.json").exists()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_staged_recovery_target_metadata_must_match_candidate_identity(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        prepared = replace(
            prepared,
            candidate_config=replace(
                prepared.candidate_config,
                target_metadata_digest="e" * 64,
            ),
        )
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = store.create_prepared(prepared)
        with pytest.raises(ForgeError) as raised:
            store.append(
                head,
                JournalTransition(JournalState.STAGED, config_recovery=descriptor),
            )
        assert raised.value.code == "journal.corrupt"
        assert not (directory / "journal-00000001.json").exists()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_staged_recovery_snapshot_must_bind_before_and_candidate(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        prepared = replace(
            prepared,
            candidate_config=replace(
                prepared.candidate_config,
                snapshot_digest="e" * 64,
            ),
        )
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = store.create_prepared(prepared)
        with pytest.raises(ForgeError) as raised:
            store.append(
                head,
                JournalTransition(JournalState.STAGED, config_recovery=descriptor),
            )
        assert raised.value.code == "journal.corrupt"
        assert not (directory / "journal-00000001.json").exists()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_rollback_action_identity_must_match_owned_path(tmp_path: Path) -> None:
    from zagrosi_forge.install.journal import PreparedTransaction

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        action = prepared.rollback_actions[0]
        mismatched = replace(
            action,
            expected_identity=(
                action.expected_identity[0],
                action.expected_identity[1] + 1,
            ),
        )
        with pytest.raises(ValueError, match="paths"):
            PreparedTransaction(
                transaction_id=prepared.transaction_id,
                effective_marketplace_id=prepared.effective_marketplace_id,
                config_transaction_digest=prepared.config_transaction_digest,
                plan_digest=prepared.plan_digest,
                runner_provenance=prepared.runner_provenance,
                install_identity=prepared.install_identity,
                before_relation_digest=prepared.before_relation_digest,
                candidate_relation_digest=prepared.candidate_relation_digest,
                before_config=prepared.before_config,
                candidate_config=prepared.candidate_config,
                identities=prepared.identities,
                transaction_owned_paths=prepared.transaction_owned_paths,
                rollback_actions=(mismatched,),
                prepared_receipt=prepared.prepared_receipt,
            )
        assert not tuple(directory.glob("journal*"))
    finally:
        store.close()
        proof.close()
        owned.close()


def test_rollback_actions_are_identity_bound_and_unique_per_target(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import PreparedTransaction, RollbackAction

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        action = prepared.rollback_actions[0]
        with pytest.raises(ValueError, match="rollback identity"):
            replace(action, expected_identity=None)

        contradictory = RollbackAction(
            action="retain",
            relative_path=action.relative_path,
            expected_identity=action.expected_identity,
        )
        with pytest.raises(ValueError, match="paths"):
            PreparedTransaction(
                transaction_id=prepared.transaction_id,
                effective_marketplace_id=prepared.effective_marketplace_id,
                config_transaction_digest=prepared.config_transaction_digest,
                plan_digest=prepared.plan_digest,
                runner_provenance=prepared.runner_provenance,
                install_identity=prepared.install_identity,
                before_relation_digest=prepared.before_relation_digest,
                candidate_relation_digest=prepared.candidate_relation_digest,
                before_config=prepared.before_config,
                candidate_config=prepared.candidate_config,
                identities=prepared.identities,
                transaction_owned_paths=prepared.transaction_owned_paths,
                rollback_actions=(action, contradictory),
                prepared_receipt=prepared.prepared_receipt,
            )
        assert not tuple(directory.glob("journal*"))
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.parametrize(
    "corruption", ["future", "minimum-reader", "truncated", "duplicate"]
)
def test_unknown_major_truncated_or_duplicate_journal_preserves_state(
    tmp_path: Path,
    corruption: str,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        store.create_prepared(_prepared(binding))
        path = directory / "journal-00000000.json"
        if corruption == "future":
            _rewrite(path, lambda value: {**value, "schema_version": "2.0"})
            expected = "journal.unsupported_schema"
        elif corruption == "minimum-reader":
            _rewrite(
                path,
                lambda value: {**value, "minimum_reader_version": "99.0.0"},
            )
            expected = "journal.unsupported_schema"
        elif corruption == "truncated":
            path.write_bytes(path.read_bytes()[:-7])
            expected = "journal.corrupt"
        else:
            raw = path.read_bytes()
            path.write_bytes(
                raw.replace(
                    b'"state":"PREPARED"',
                    b'"state":"PREPARED","state":"PREPARED"',
                    1,
                )
            )
            expected = "journal.corrupt"
        retained = path.read_bytes()
        with pytest.raises(ForgeError) as raised:
            store.load()
        assert raised.value.code == expected
        assert raised.value.exit_category == 14
        assert path.read_bytes() == retained
    finally:
        store.close()
        proof.close()
        owned.close()


def test_impossible_transition_and_broken_digest_chain_preserve_all(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        head = store.create_prepared(prepared)
        store.append(
            head,
            JournalTransition(
                JournalState.STAGED,
                config_recovery=_config_recovery_descriptor(prepared.transaction_id),
            ),
        )
        second = directory / "journal-00000001.json"
        _rewrite(second, lambda value: {**value, "state": "PUBLISHED"})
        before = tuple(
            (path.name, path.stat().st_ino, path.read_bytes())
            for path in sorted(directory.glob("journal-*.json"))
        )
        with pytest.raises(ForgeError) as raised:
            store.load()
        assert raised.value.code == "journal.corrupt"
        assert (
            tuple(
                (path.name, path.stat().st_ino, path.read_bytes())
                for path in sorted(directory.glob("journal-*.json"))
            )
            == before
        )

        _rewrite(
            second,
            lambda value: {
                **value,
                "state": "STAGED",
                "previous_record_digest": "e" * 64,
            },
        )
        retained = second.read_bytes()
        with pytest.raises(ForgeError) as broken:
            store.load()
        assert broken.value.code == "journal.corrupt"
        assert second.read_bytes() == retained
    finally:
        store.close()
        proof.close()
        owned.close()


def test_pending_loader_rejects_caller_supplied_store_capabilities(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import load_pending

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        store.create_prepared(_prepared(binding))
        before = tuple((path.name, path.read_bytes()) for path in directory.iterdir())
        with pytest.raises(TypeError, match="OwnedRoot"):
            load_pending((store,))
        assert (
            tuple((path.name, path.read_bytes()) for path in directory.iterdir())
            == before
        )
    finally:
        store.close()
        proof.close()
        owned.close()


def test_pending_loader_discovers_live_journal_through_read_only_authority(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import load_pending

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        expected = store.create_prepared(_prepared(binding))
        store.close()
        loaded = load_pending(owned)
        assert len(loaded) == 1
        assert loaded[0].records[-1].transaction_id == binding.transaction_id
        assert loaded[0].head.record_digest == expected.record_digest
    finally:
        store.close()
        proof.close()
        owned.close()


def test_loaded_journal_rejects_forged_decoded_rollback_authority(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import RollbackAction, TransactionOwnedPath

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        journal = store.load()
        record = journal.records[-1]
        foreign_path = TransactionOwnedPath(
            role="foreign-generation",
            relative_path="foreign/unrecorded-generation",
            expected_identity=(99, 100),
        )
        foreign_action = RollbackAction(
            action="quarantine-if-owned",
            relative_path=foreign_path.relative_path,
            expected_identity=foreign_path.expected_identity,
        )
        forged = replace(
            prepared,
            transaction_owned_paths=prepared.transaction_owned_paths + (foreign_path,),
            rollback_actions=prepared.rollback_actions + (foreign_action,),
        )

        with pytest.raises(TypeError):
            replace(record, prepared=forged)
        with pytest.raises(TypeError):
            replace(journal, records=journal.records)

        object.__setattr__(record, "prepared", forged)
        try:
            with pytest.raises(TypeError, match="authority changed"):
                journal._require_valid()
        finally:
            object.__setattr__(record, "prepared", prepared)
        journal._require_valid()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_pending_loader_rechecks_journal_contents_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalStore, load_pending

    store, proof, owned, directory, binding = _store(tmp_path)
    canonical = directory / "journal-00000000.json"
    displaced = directory / "displaced-journal.json"
    try:
        store.create_prepared(_prepared(binding))
        store.close()
        original = JournalStore._load_with_observations
        calls = 0

        def load_then_inject(selected: JournalStore):
            nonlocal calls
            calls += 1
            loaded = original(selected)
            if calls == 2:
                canonical.rename(displaced)
                _write_private_file(directory, canonical.name, b"{}\n")
            return loaded

        monkeypatch.setattr(
            JournalStore,
            "_load_with_observations",
            load_then_inject,
        )
        with pytest.raises(ForgeError) as raised:
            load_pending(owned)
        assert raised.value.code == "journal.corrupt"
        assert canonical.read_bytes() == b"{}\n"
        assert displaced.is_file()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_journal_aggregate_quota_maps_to_limit_without_publication(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.policies import LIMIT_POLICY

    store, proof, owned, directory, _binding = _store(tmp_path)
    try:
        cases = (
            (b"x" * (LIMIT_POLICY.value("journal_record_bytes") + 1), 0),
            (b"{}\n", LIMIT_POLICY.value("journal_total_bytes")),
        )
        for raw, current_size in cases:
            with pytest.raises(ForgeError) as raised:
                store._publish(raw, sequence=0, current_size=current_size)
            assert raised.value.code == "journal.limit_exceeded"
            assert raised.value.exit_category == 14
        assert not tuple(directory.glob("journal*"))
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.parametrize("quota", ["record-bytes", "record-count"])
def test_actual_journal_quota_excess_is_stable_and_preserves_all(
    tmp_path: Path,
    quota: str,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.policies import LIMIT_POLICY

    if os.name == "nt":
        pytest.skip("native record fabrication is POSIX-specific")
    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        store.create_prepared(_prepared(binding))
        if quota == "record-bytes":
            extra = directory / "journal-00000001.json"
            extra.write_bytes(b"x" * (LIMIT_POLICY.value("journal_record_bytes") + 1))
            extra.chmod(0o600)
        else:
            for sequence in range(1, LIMIT_POLICY.value("journal_records") + 1):
                extra = directory / f"journal-{sequence:08d}.json"
                extra.write_bytes(b"{}\n")
                extra.chmod(0o600)
        before = tuple(
            (path.name, path.stat().st_ino, path.read_bytes())
            for path in sorted(directory.glob("journal*"))
        )
        with pytest.raises(ForgeError) as raised:
            store.load()
        assert raised.value.code == "journal.limit_exceeded"
        assert raised.value.exit_category == 14
        assert (
            tuple(
                (path.name, path.stat().st_ino, path.read_bytes())
                for path in sorted(directory.glob("journal*"))
            )
            == before
        )
    finally:
        store.close()
        proof.close()
        owned.close()


def test_windows_record_fingerprint_comparison_includes_all_metadata() -> None:
    import zagrosi_forge.install.paths as paths
    from zagrosi_forge.install.journal import _windows_record_unchanged

    before = paths._WindowsHandleStatus(
        identity=(1, 2),
        attributes=0x20,
        size=100,
        link_count=1,
        creation_time=10,
        last_write_time=11,
        change_time=12,
    )
    assert _windows_record_unchanged(before, before)
    for field in (
        "identity",
        "attributes",
        "size",
        "link_count",
        "creation_time",
        "last_write_time",
        "change_time",
    ):
        value = getattr(before, field)
        changed = (1, 3) if field == "identity" else value + 1
        assert not _windows_record_unchanged(
            before, replace(before, **{field: changed})
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows authorization recheck")
def test_windows_record_authorization_is_rechecked_after_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.paths as paths

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        store.create_prepared(_prepared(binding))
        original = paths._windows_private_authorization
        calls = 0

        def counted(handle: int, *, exact: bool) -> bool:
            nonlocal calls
            calls += 1
            return original(handle, exact=exact)

        monkeypatch.setattr(paths, "_windows_private_authorization", counted)
        store.load()
        assert calls >= 2
    finally:
        store.close()
        proof.close()
        owned.close()


def test_journal_rejects_raw_paths_and_link_records_without_deleting(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalStore

    store, proof, owned, directory, _binding = _store(tmp_path)
    try:
        with pytest.raises(TypeError, match="transaction journal access"):
            JournalStore(directory, proof)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="transaction journal access"):
            JournalStore(proof, proof)
        if os.name == "nt":
            pytest.skip(
                "Windows reparse-point coverage runs in the path authority suite"
            )
        target = tmp_path / "outside.json"
        target.write_bytes(b"{}\n")
        linked = directory / "journal-00000000.json"
        linked.symlink_to(target)
        with pytest.raises(ForgeError) as raised:
            store.load()
        assert raised.value.code == "journal.corrupt"
        assert linked.is_symlink()
        assert target.read_bytes() == b"{}\n"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_journal_rejects_unknown_and_hard_linked_records_without_cleanup(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError

    if os.name == "nt":
        pytest.skip("Windows link-count coverage runs in the path authority suite")
    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        unknown = directory / "journal.json"
        unknown.write_bytes(b"{}\n")
        unknown.chmod(0o600)
        with pytest.raises(ForgeError) as unknown_error:
            store.load()
        assert unknown_error.value.code == "journal.corrupt"
        assert unknown.read_bytes() == b"{}\n"
        unknown.unlink()

        store.create_prepared(_prepared(binding))
        record = directory / "journal-00000000.json"
        retained = record.read_bytes()
        outside = tmp_path / "hardlink-source.json"
        outside.write_bytes(retained)
        outside.chmod(0o600)
        record.unlink()
        os.link(outside, record)
        before = (record.stat().st_nlink, record.read_bytes())
        with pytest.raises(ForgeError) as linked:
            store.load()
        assert linked.value.code == "journal.corrupt"
        assert (record.stat().st_nlink, record.read_bytes()) == before
        assert outside.read_bytes() == retained
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO behavior")
def test_journal_fifo_substitution_fails_without_blocking_or_cleanup(
    tmp_path: Path,
) -> None:
    import signal
    import stat
    import time

    from zagrosi_forge.install.contracts import ForgeError

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        store.create_prepared(_prepared(binding))
        record = directory / "journal-00000000.json"
        record.unlink()
        os.mkfifo(record, 0o600)
        record.chmod(0o600)

        previous = signal.getsignal(signal.SIGALRM)

        def expired(_signum, _frame) -> None:
            raise TimeoutError("journal FIFO open blocked")

        started = time.monotonic()
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, 3.0)
        try:
            with pytest.raises(ForgeError) as raised:
                store.load()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, previous)
        assert time.monotonic() - started < 1.0
        assert raised.value.code == "journal.corrupt"
        assert stat.S_ISFIFO(record.lstat().st_mode)
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory rename race")
def test_journal_load_rejects_detached_transaction_namespace_after_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalStore

    store, proof, owned, directory, binding = _store(tmp_path)
    detached = directory.with_name(f"{directory.name}.detached")
    try:
        store.create_prepared(_prepared(binding))
        retained = (directory / "journal-00000000.json").read_bytes()
        original = JournalStore._read_posix
        moved = False

        def detach_after_read(self, name: str) -> bytes:
            nonlocal moved
            raw = original(self, name)
            if not moved:
                directory.rename(detached)
                moved = True
            return raw

        monkeypatch.setattr(JournalStore, "_read_posix", detach_after_read)
        with pytest.raises(ForgeError) as raised:
            store.load()
        assert raised.value.code == "journal.corrupt"
        assert not directory.exists()
        assert (detached / "journal-00000000.json").read_bytes() == retained
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_plan_without_journals_is_no_recovery_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    def reject_effect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("pure recovery planning attempted a filesystem effect")

    snapshot = RecoverySnapshot(journals=(), current_config=None)
    with monkeypatch.context() as effects:
        effects.setattr(os, "open", reject_effect)
        effects.setattr(Path, "mkdir", reject_effect)
        effects.setattr(Path, "write_bytes", reject_effect)
        effects.setattr(Path, "unlink", reject_effect)
        first = plan_recovery(snapshot)
        second = plan_recovery(snapshot)

    assert first == second
    assert first.disposition is RecoveryDisposition.NO_RECOVERY
    assert first.transaction_ids == ()
    assert first.rollback_actions == ()
    assert first.error_code is None
    assert len(first.plan_digest) == 64
    assert set(first.plan_digest) <= set("0123456789abcdef")


def test_recovery_snapshot_revalidates_loaded_journal_authority(
    tmp_path: Path,
) -> None:
    from enum import Enum

    from zagrosi_forge.install.journal import RollbackAction
    from zagrosi_forge.install.recovery import RecoverySnapshot

    class FakeAction(str, Enum):
        QUARANTINE = "quarantine-if-owned"

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        journal = store.load()
        original = journal.records[-1].prepared.rollback_actions
        injected = RollbackAction(
            action="quarantine-if-owned",
            relative_path="foreign/unrecorded-generation",
            expected_identity=(99, 100),
        )
        object.__setattr__(
            journal.records[-1].prepared,
            "rollback_actions",
            original + (injected,),
        )
        try:
            with pytest.raises(TypeError, match="RecoverySnapshot"):
                RecoverySnapshot(
                    journals=(journal,),
                    current_config=prepared.before_config,
                )
        finally:
            object.__setattr__(
                journal.records[-1].prepared,
                "rollback_actions",
                original,
            )
        journal._require_valid()

        action = journal.records[-1].prepared.rollback_actions[0]
        original_action = action.action
        object.__setattr__(action, "action", FakeAction.QUARANTINE)
        try:
            with pytest.raises(TypeError, match="RecoverySnapshot"):
                RecoverySnapshot(
                    journals=(journal,),
                    current_config=prepared.before_config,
                )
        finally:
            object.__setattr__(action, "action", original_action)
        journal._require_valid()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_rejects_evidence_change_between_decision_and_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.recovery as recovery

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        journal = store.load()
        current = prepared.before_config
        snapshot = recovery.RecoverySnapshot(
            journals=(journal,),
            current_config=current,
        )
        original = recovery._journal_disposition
        original_digest = current.byte_digest

        def mutate_after_decision(journal_capture, config_capture):
            decision = original(journal_capture, config_capture)
            object.__setattr__(current, "byte_digest", "d" * 64)
            return decision

        monkeypatch.setattr(
            recovery,
            "_journal_disposition",
            mutate_after_decision,
        )
        try:
            with pytest.raises(TypeError, match="RecoverySnapshot"):
                recovery.plan_recovery(snapshot)
        finally:
            object.__setattr__(current, "byte_digest", original_digest)
        snapshot._require_valid()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_plan_rejects_digest_equivalent_type_tampering() -> None:
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    class FakeDisposition:
        value = RecoveryDisposition.NO_RECOVERY.value

    plan = plan_recovery(RecoverySnapshot(journals=(), current_config=None))
    for field, changed in (
        ("transaction_ids", []),
        ("rollback_actions", []),
        ("disposition", FakeDisposition()),
    ):
        original = getattr(plan, field)
        object.__setattr__(plan, field, changed)
        try:
            with pytest.raises(TypeError, match="RecoveryPlan"):
                plan._require_valid()
        finally:
            object.__setattr__(plan, field, original)
    plan._require_valid()


@pytest.mark.parametrize(
    ("first_terminal", "second_terminal", "expected_disposition"),
    (
        (False, False, "operator_conflict"),
        (False, True, "operator_conflict"),
        (True, True, "no_recovery"),
    ),
)
def test_multiple_journals_never_merge_unfinished_rollback_authority(
    tmp_path: Path,
    first_terminal: bool,
    second_terminal: bool,
    expected_disposition: str,
) -> None:
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _private_directory(first_root)
    _private_directory(second_root)
    first = _store(first_root, transaction_id="tx-" + "a" * 32)
    second = _store(second_root, transaction_id="tx-" + "b" * 32)
    first_store, first_proof, first_owned, _first_directory, first_binding = first
    second_store, second_proof, second_owned, _second_directory, second_binding = second
    try:
        first_prepared = _prepared(first_binding)
        second_prepared = _prepared(second_binding)
        if first_terminal:
            _advance_to_finalized(
                first_store,
                first_prepared,
                _config_recovery_descriptor(first_prepared.transaction_id),
            )
        else:
            first_store.create_prepared(first_prepared)
        if second_terminal:
            _advance_to_finalized(
                second_store,
                second_prepared,
                _config_recovery_descriptor(second_prepared.transaction_id),
            )
        else:
            second_store.create_prepared(second_prepared)

        plan = plan_recovery(
            RecoverySnapshot(
                journals=(first_store.load(), second_store.load()),
                current_config=first_prepared.before_config,
            )
        )

        assert plan.disposition.value == expected_disposition
        assert plan.rollback_actions == ()
        if plan.disposition is RecoveryDisposition.OPERATOR_CONFLICT:
            assert plan.transaction_ids == (
                first_prepared.transaction_id,
                second_prepared.transaction_id,
            )
            assert plan.error_code == "recovery.operator_conflict"
        else:
            assert plan.transaction_ids == ()
            assert plan.error_code is None
    finally:
        second_store.close()
        second_proof.close()
        second_owned.close()
        first_store.close()
        first_proof.close()
        first_owned.close()


def test_recovery_supports_the_full_bounded_pending_inventory(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.policies import LIMIT_POLICY
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    resources = []
    journals = []
    prepared_transactions = []
    try:
        for index in range(LIMIT_POLICY.value("journal_records")):
            root = tmp_path / f"inventory-{index:02d}"
            _private_directory(root)
            store, proof, owned, _directory, binding = _store(
                root,
                transaction_id=f"tx-{index:032x}",
            )
            resources.append((store, proof, owned))
            prepared = _prepared(binding)
            prepared_transactions.append(prepared)
            store.create_prepared(prepared)
            journals.append(store.load())

        plan = plan_recovery(
            RecoverySnapshot(
                journals=tuple(journals),
                current_config=prepared_transactions[0].before_config,
            )
        )

        assert plan.disposition is RecoveryDisposition.OPERATOR_CONFLICT
        assert plan.transaction_ids == tuple(
            prepared.transaction_id for prepared in prepared_transactions
        )
        assert plan.rollback_actions == ()
        assert plan.error_code == "recovery.operator_conflict"
    finally:
        for store, proof, owned in reversed(resources):
            store.close()
            proof.close()
            owned.close()


def test_before_identity_recovery_rolls_back_only_recorded_owned_candidate(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        journal = store.load()
        assert journal.records[-1].prepared == prepared

        plan = plan_recovery(
            RecoverySnapshot(
                journals=(journal,),
                current_config=prepared.before_config,
            )
        )

        assert plan.disposition is RecoveryDisposition.ROLLBACK_CANDIDATE
        assert plan.transaction_ids == (prepared.transaction_id,)
        assert plan.rollback_actions == prepared.rollback_actions
        assert all(
            any(
                owned_path.relative_path == action.relative_path
                and owned_path.expected_identity == action.expected_identity
                for owned_path in prepared.transaction_owned_paths
            )
            for action in plan.rollback_actions
        )
        assert plan.error_code is None
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_plan_rejects_digest_equivalent_nested_action_type(
    tmp_path: Path,
) -> None:
    from enum import Enum

    from zagrosi_forge.install.recovery import RecoverySnapshot, plan_recovery

    class FakeAction(str, Enum):
        QUARANTINE = "quarantine-if-owned"

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        plan = plan_recovery(
            RecoverySnapshot(
                journals=(store.load(),),
                current_config=prepared.before_config,
            )
        )
        action = plan.rollback_actions[0]
        original = action.action
        object.__setattr__(action, "action", FakeAction.QUARANTINE)
        try:
            with pytest.raises(TypeError, match="RecoveryPlan"):
                plan._require_valid()
        finally:
            object.__setattr__(action, "action", original)
        plan._require_valid()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_preserves_valid_internal_receipt_reference_domain(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import RollbackAction, TransactionOwnedPath
    from zagrosi_forge.install.ownership import committed_receipt_reference
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        reference = committed_receipt_reference(
            prepared.effective_marketplace_id,
            prepared.install_identity,
        ).value
        retained = TransactionOwnedPath(
            role="committed-receipt",
            relative_path=reference,
            expected_identity=None,
        )
        retain = RollbackAction(
            action="retain",
            relative_path=reference,
            expected_identity=None,
        )
        prepared = replace(
            prepared,
            transaction_owned_paths=prepared.transaction_owned_paths + (retained,),
            rollback_actions=prepared.rollback_actions + (retain,),
        )
        store.create_prepared(prepared)

        plan = plan_recovery(
            RecoverySnapshot(
                journals=(store.load(),),
                current_config=prepared.before_config,
            )
        )

        assert plan.disposition is RecoveryDisposition.ROLLBACK_CANDIDATE
        assert retain in plan.rollback_actions
        assert plan.error_code is None
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.parametrize(
    "changed",
    (
        {"leaf_identity": (11, 99)},
        {"parent_identity": (11, 99)},
        {"byte_digest": "d" * 64},
        {"semantic_digest": "e" * 64},
        {"metadata_fingerprint": "f" * 64},
        {"snapshot_digest": "0" * 64},
        {"target_metadata_digest": "9" * 64},
    ),
)
def test_any_config_identity_drift_requires_operator(
    tmp_path: Path,
    changed: dict[str, object],
) -> None:
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        journal = store.load()
        current = replace(prepared.before_config, **changed)

        plan = plan_recovery(
            RecoverySnapshot(
                journals=(journal,),
                current_config=current,
            )
        )

        assert plan.disposition is RecoveryDisposition.OPERATOR_CONFLICT
        assert plan.transaction_ids == (prepared.transaction_id,)
        assert plan.rollback_actions == ()
        assert plan.error_code == "recovery.operator_conflict"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_historical_publication_does_not_authorize_recovery_finalization(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        _advance_to_commit_intent(store, prepared, descriptor)
        journal = store.load()
        current = _config_result(prepared, descriptor)

        plan = plan_recovery(
            RecoverySnapshot(
                journals=(journal,),
                current_config=current,
            )
        )

        assert plan.disposition is RecoveryDisposition.OPERATOR_CONFLICT
        assert plan.transaction_ids == (prepared.transaction_id,)
        assert plan.rollback_actions == ()
        assert plan.error_code == "recovery.operator_conflict"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_candidate_relation_without_verified_published_evidence_requires_operator(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        journal = store.load()
        current = replace(
            prepared.candidate_config,
            leaf_identity=(11, 15),
        )

        plan = plan_recovery(
            RecoverySnapshot(
                journals=(journal,),
                current_config=current,
            )
        )

        assert plan.disposition is RecoveryDisposition.OPERATOR_CONFLICT
        assert plan.transaction_ids == (prepared.transaction_id,)
        assert plan.rollback_actions == ()
        assert plan.error_code == "recovery.operator_conflict"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_observation_binds_live_config_and_pending_journals(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        observe_current_config_identity,
        observe_recovery_snapshot,
        plan_recovery,
    )

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    try:
        current = observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)

        snapshot = observe_recovery_snapshot(
            authority=authority,
            owned_root=owned,
        )
        plan = plan_recovery(snapshot)

        assert snapshot.current_config == current
        assert len(snapshot.journals) == 1
        assert snapshot.journals[0].records[-1].prepared == prepared
        assert plan.disposition is RecoveryDisposition.ROLLBACK_CANDIDATE
        assert plan.transaction_ids == (prepared.transaction_id,)
        assert plan.rollback_actions == prepared.rollback_actions
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_observation_digest_binds_transaction_location(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import load_pending
    from zagrosi_forge.install.lock import acquire_install_lock
    from zagrosi_forge.install.ownership import (
        discover_pending_transactions,
        prove_transaction_owned,
        quarantine_owned,
        rebind_persistent_transaction,
    )
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        lock_recovery_plan,
        observe_current_config_identity,
        observe_recovery_snapshot,
        plan_recovery,
    )

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    ticket = None
    try:
        current = observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        live_snapshot = observe_recovery_snapshot(
            authority=authority,
            owned_root=owned,
        )
        live_plan = plan_recovery(live_snapshot)
        assert live_plan.disposition is RecoveryDisposition.ROLLBACK_CANDIDATE
        live_head = live_snapshot.journals[0].head.record_digest
        live_inventory = discover_pending_transactions(owned).unwrap()
        store.close()
        proof.close()

        rebound = rebind_persistent_transaction(owned, binding=binding).unwrap()
        with rebound:
            assert rebound.claim is not None
            path = authority.prove_descendant(
                owned,
                rebound.claim.relative,
                expected_depth=3,
            ).unwrap()
            try:
                ownership = prove_transaction_owned(
                    path,
                    claim=rebound.claim,
                ).unwrap()
                ticket = quarantine_owned(
                    ownership,
                    transaction_id=prepared.transaction_id,
                ).unwrap()
            finally:
                path.close()

        quarantined_journals = load_pending(owned)
        with pytest.raises(TypeError, match="inventory"):
            recovery._observed_inventory_digest(
                live_inventory,
                quarantined_journals,
            )
        quarantined_snapshot = observe_recovery_snapshot(
            authority=authority,
            owned_root=owned,
        )
        quarantined_plan = plan_recovery(quarantined_snapshot)

        assert quarantined_snapshot.journals[0].head.record_digest == live_head
        assert quarantined_plan.disposition is RecoveryDisposition.ROLLBACK_CANDIDATE
        assert quarantined_snapshot.snapshot_digest != live_snapshot.snapshot_digest
        assert quarantined_plan.plan_digest != live_plan.plan_digest
        with pytest.raises(ForgeError) as changed:
            lock_recovery_plan(
                live_plan,
                authority=authority,
                owned_root=owned,
                runner=_runner(),
                timeout_seconds=0.1,
            )
        assert changed.value.code == "transaction.plan_changed"
        with acquire_install_lock(owned, timeout_seconds=0.1):
            pass
    finally:
        if ticket is not None:
            ticket.close()
        store.close()
        proof.close()
        owned.close()


def test_recovery_observation_rejects_same_id_journal_from_other_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.paths import PlatformPathAuthority

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _private_directory(first_root)
    _private_directory(second_root)
    first_authority = PlatformPathAuthority()
    second_authority = PlatformPathAuthority()
    first = _store(first_root, authority=first_authority)
    second = _store(second_root, authority=second_authority)
    first_store, first_proof, first_owned, _first_directory, first_binding = first
    second_store, second_proof, second_owned, _second_directory, second_binding = second
    try:
        first_current = recovery.observe_current_config_identity(
            authority=first_authority,
            owned_root=first_owned,
        )
        second_current = recovery.observe_current_config_identity(
            authority=second_authority,
            owned_root=second_owned,
        )
        first_store.create_prepared(
            _bind_prepared_to_current_config(
                _prepared(first_binding),
                first_current,
            )
        )
        second_store.create_prepared(
            _bind_prepared_to_current_config(
                _prepared(second_binding),
                second_current,
            )
        )
        foreign = second_store.load()
        assert first_binding.transaction_id == second_binding.transaction_id
        assert (
            first_binding.canonical_projection()
            != second_binding.canonical_projection()
        )
        monkeypatch.setattr(recovery, "load_pending", lambda _root: (foreign,))

        with pytest.raises(ForgeError) as changed:
            recovery.observe_recovery_snapshot(
                authority=first_authority,
                owned_root=first_owned,
            )
        assert changed.value.code == "transaction.plan_changed"
    finally:
        second_store.close()
        second_proof.close()
        second_owned.close()
        first_store.close()
        first_proof.close()
        first_owned.close()


@pytest.mark.parametrize("missing_side", ("observation", "journal"))
def test_recovery_observation_rejects_inventory_cardinality_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_side: str,
) -> None:
    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import load_pending
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        inventory = recovery._discover_inventory(owned)
        journals = load_pending(owned)
        assert len(inventory) == len(journals) == 1
        if missing_side == "observation":
            monkeypatch.setattr(recovery, "_discover_inventory", lambda _root: ())
        else:
            monkeypatch.setattr(recovery, "load_pending", lambda _root: ())

        with pytest.raises(ForgeError) as changed:
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        assert changed.value.code == "transaction.plan_changed"
    finally:
        store.close()
        proof.close()
        owned.close()


def test_recovery_observation_rejects_loaded_journal_access_digest_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    loaded = None
    original = None
    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        loaded = store.load()
        original = loaded.access_digest
        object.__setattr__(loaded, "access_digest", "f" * 64)
        monkeypatch.setattr(recovery, "load_pending", lambda _root: (loaded,))

        with pytest.raises(TypeError, match="loaded journal authority"):
            loaded._require_valid()
        with pytest.raises(ForgeError) as changed:
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        assert changed.value.code == "transaction.plan_changed"
    finally:
        if loaded is not None and original is not None:
            object.__setattr__(loaded, "access_digest", original)
            loaded._require_valid()
        store.close()
        proof.close()
        owned.close()


def test_live_recovery_snapshot_rejects_inventory_digest_tampering(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.recovery import (
        observe_current_config_identity,
        observe_recovery_snapshot,
    )

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    try:
        current = observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        snapshot = observe_recovery_snapshot(
            authority=authority,
            owned_root=owned,
        )
        original = snapshot._inventory_digest
        object.__setattr__(snapshot, "_inventory_digest", "f" * 64)
        try:
            with pytest.raises(TypeError, match="RecoverySnapshot"):
                snapshot._require_valid()
        finally:
            object.__setattr__(snapshot, "_inventory_digest", original)
        snapshot._require_valid()
    finally:
        store.close()
        proof.close()
        owned.close()
