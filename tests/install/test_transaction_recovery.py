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


def _prepared_with_ordered_rollback(binding):
    return _prepared_with_rollback_action_count(binding, 2)


def _prepared_with_rollback_action_count(binding, count: int):
    from zagrosi_forge.install.journal import RollbackAction, TransactionOwnedPath

    prepared = _prepared(binding)
    snapshot = next(
        path
        for path in prepared.transaction_owned_paths
        if path.role == "config-snapshot"
    )
    retained_paths = (
        snapshot,
        *(
            TransactionOwnedPath(
                role=f"rollback-slot-{index:02d}",
                relative_path=(
                    f"{binding.root_relative}/rollback-slot-{index:02d}.retained"
                ),
                expected_identity=None,
            )
            for index in range(1, count - 1)
        ),
    )
    retained_actions = tuple(
        RollbackAction(
            action="retain",
            relative_path=path.relative_path,
            expected_identity=path.expected_identity,
        )
        for path in retained_paths
    )
    root_quarantine = next(
        action
        for action in prepared.rollback_actions
        if action.relative_path == binding.root_relative
    )
    declared = retained_actions + (root_quarantine,)
    additional_owned = retained_paths[1:]
    return (
        replace(
            prepared,
            transaction_owned_paths=(
                prepared.transaction_owned_paths + additional_owned
            ),
            rollback_actions=declared,
        ),
        declared,
    )


def _rollback_intent(action, index: int):
    from zagrosi_forge.install.journal import JournalRollbackEvent

    return JournalRollbackEvent(
        action_index=index,
        action_digest=action.action_digest,
    )


def _rollback_completion(action, index: int, binding):
    from zagrosi_forge.install.journal import JournalRollbackEvent

    if action.action == "retain":
        return JournalRollbackEvent(
            action_index=index,
            action_digest=action.action_digest,
            outcome="retained",
        )
    return JournalRollbackEvent(
        action_index=index,
        action_digest=action.action_digest,
        outcome="quarantined",
        observed_identity=action.expected_identity,
        recovery_reference=binding.quarantine_relative,
    )


def _append_rollback_pair(store, head, action, index: int, binding):
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    head = store.append(
        head,
        JournalTransition(
            JournalState.ROLLBACK_ACTION_INTENT,
            rollback_event=_rollback_intent(action, index),
        ),
    )
    return store.append(
        head,
        JournalTransition(
            JournalState.ROLLBACK_ACTION_COMPLETED,
            rollback_event=_rollback_completion(action, index, binding),
        ),
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
        prepared = _prepared(binding)
        root_action = prepared.rollback_actions[-1]
        root_index = len(prepared.rollback_actions) - 1
        head = store.create_prepared(prepared)
        head = store.append(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_INTENT,
                rollback_event=_rollback_intent(root_action, root_index),
            ),
        )
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
            "rollback_event",
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


def test_prepared_requires_identity_bound_transaction_root_rollback(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import RollbackAction

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        snapshot = next(
            path
            for path in prepared.transaction_owned_paths
            if path.role == "config-snapshot"
        )
        retain_only = RollbackAction(
            action="retain",
            relative_path=snapshot.relative_path,
            expected_identity=snapshot.expected_identity,
        )

        with pytest.raises(ValueError, match="persistent transaction rollback"):
            replace(prepared, rollback_actions=(retain_only,))

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
            action="retain",
            relative_path=foreign_path.relative_path,
            expected_identity=foreign_path.expected_identity,
        )
        forged = replace(
            prepared,
            transaction_owned_paths=prepared.transaction_owned_paths + (foreign_path,),
            rollback_actions=(
                *prepared.rollback_actions[:-1],
                foreign_action,
                prepared.rollback_actions[-1],
            ),
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


def test_prepared_reserves_complete_rollback_wal_before_publication(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, _declared = _prepared_with_rollback_action_count(binding, 12)
        prepared = replace(
            prepared,
            runner_provenance=replace(
                prepared.runner_provenance,
                origin="x" * 400_000,
            ),
        )

        with pytest.raises(ForgeError) as raised:
            store.create_prepared(prepared)

        assert raised.value.code == "journal.limit_exceeded"
        assert not tuple(directory.glob("journal*"))
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


def test_recovery_plan_rejects_missing_transaction_root_rollback_authority(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import RollbackAction
    import zagrosi_forge.install.recovery as recovery

    store, proof, owned, _directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        plan = recovery.plan_recovery(
            recovery.RecoverySnapshot(
                journals=(store.load(),),
                current_config=prepared.before_config,
            )
        )
        original = plan.rollback_actions
        snapshot = next(
            path
            for path in prepared.transaction_owned_paths
            if path.role == "config-snapshot"
        )
        retain_only = RollbackAction(
            action="retain",
            relative_path=snapshot.relative_path,
            expected_identity=snapshot.expected_identity,
        )
        with pytest.raises(TypeError, match="RecoveryPlan"):
            recovery.RecoveryPlan(
                snapshot_digest=plan.snapshot_digest,
                disposition=plan.disposition,
                transaction_ids=plan.transaction_ids,
                rollback_actions=(retain_only,),
                next_action_index=plan.next_action_index,
                error_code=plan.error_code,
                _token=recovery._PLAN_TOKEN,
            )
        assert original == prepared.rollback_actions
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
            rollback_actions=(
                *prepared.rollback_actions[:-1],
                retain,
                prepared.rollback_actions[-1],
            ),
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
    from zagrosi_forge.install.journal import (
        JournalState,
        JournalTransition,
        load_pending,
    )
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
        head = store.create_prepared(prepared)
        root_action = prepared.rollback_actions[-1]
        store.append(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_INTENT,
                rollback_event=_rollback_intent(
                    root_action,
                    len(prepared.rollback_actions) - 1,
                ),
            ),
        )
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


def test_locked_recovery_rejects_structural_snapshot_without_live_inventory(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.lock import acquire_install_lock
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.recovery import (
        RecoverySnapshot,
        lock_recovery_plan,
        observe_current_config_identity,
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
        structural = plan_recovery(
            RecoverySnapshot(
                journals=(store.load(),),
                current_config=current,
            )
        )

        with pytest.raises(ForgeError) as changed:
            lock_recovery_plan(
                structural,
                authority=authority,
                owned_root=owned,
                runner=_runner(),
                timeout_seconds=0.1,
            )
        assert changed.value.code == "transaction.plan_changed"
        with acquire_install_lock(owned, timeout_seconds=0.1):
            pass
    finally:
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


def test_locked_recovery_reloads_and_reproduces_the_exact_plan(
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
    acquired = []
    observed_while_held = []
    real_acquire = recovery.acquire_install_lock
    real_observe = recovery.observe_recovery_snapshot

    def record_acquire(*args, **kwargs):
        held = real_acquire(*args, **kwargs)
        acquired.append(held)
        return held

    def record_observation(*args, **kwargs):
        assert acquired
        assert not acquired[-1]._released
        observed_while_held.append(True)
        return real_observe(*args, **kwargs)

    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = recovery.plan_recovery(
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        monkeypatch.setattr(recovery, "acquire_install_lock", record_acquire)
        monkeypatch.setattr(recovery, "observe_recovery_snapshot", record_observation)

        with recovery.lock_recovery_plan(
            plan,
            authority=authority,
            owned_root=owned,
            runner=_runner(),
            timeout_seconds=0.1,
        ) as locked:
            assert locked.plan == plan
            assert locked.revalidate() == plan
            assert not locked.closed

        assert acquired[-1]._released
        assert observed_while_held
        assert locked.closed
        with pytest.raises(ForgeError, match="closed"):
            locked.revalidate()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_locked_recovery_releases_lock_when_second_reproduction_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.lock import acquire_install_lock
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    observation_count = 0
    real_observe = recovery.observe_recovery_snapshot
    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = recovery.plan_recovery(
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )

        def fail_second_observation(*args, **kwargs):
            nonlocal observation_count
            observation_count += 1
            if observation_count == 2:
                raise recovery._plan_changed("Injected second reproduction failure.")
            return real_observe(*args, **kwargs)

        monkeypatch.setattr(
            recovery,
            "observe_recovery_snapshot",
            fail_second_observation,
        )
        with pytest.raises(ForgeError) as changed:
            recovery.lock_recovery_plan(
                plan,
                authority=authority,
                owned_root=owned,
                runner=_runner(),
                timeout_seconds=0.1,
            )
        assert changed.value.code == "transaction.plan_changed"
        assert observation_count == 2
        with acquire_install_lock(owned, timeout_seconds=0.1):
            pass
    finally:
        store.close()
        proof.close()
        owned.close()


def test_locked_recovery_rejects_stale_config_before_returning_authority(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.lock import acquire_install_lock
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.recovery import (
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
    try:
        current = observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = plan_recovery(
            observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        _write_private_file(tmp_path / "codex-home", "config.toml", b"# changed\n")

        with pytest.raises(ForgeError) as changed:
            lock_recovery_plan(
                plan,
                authority=authority,
                owned_root=owned,
                runner=_runner(),
                timeout_seconds=0.1,
            )
        assert changed.value.code == "transaction.plan_changed"
        with acquire_install_lock(owned, timeout_seconds=0.1):
            pass
    finally:
        store.close()
        proof.close()
        owned.close()


def test_locked_recovery_rejects_same_byte_config_replacement(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.lock import acquire_install_lock
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.recovery import (
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
    home = tmp_path / "codex-home"
    raw = b"# stable config bytes\n"
    try:
        config = _write_private_file(home, "config.toml", raw)
        current = observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = plan_recovery(
            observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        replacement = _write_private_file(home, "config.replacement", raw)
        os.replace(replacement, config)
        replaced = observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        assert replaced.byte_digest == current.byte_digest
        assert replaced.leaf_identity != current.leaf_identity

        with pytest.raises(ForgeError) as changed:
            lock_recovery_plan(
                plan,
                authority=authority,
                owned_root=owned,
                runner=_runner(),
                timeout_seconds=0.1,
            )
        assert changed.value.code == "transaction.plan_changed"
        with acquire_install_lock(owned, timeout_seconds=0.1):
            pass
    finally:
        store.close()
        proof.close()
        owned.close()


def test_locked_recovery_revalidation_detects_change_while_lock_is_held(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.recovery import (
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
    try:
        current = observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = plan_recovery(
            observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )

        with lock_recovery_plan(
            plan,
            authority=authority,
            owned_root=owned,
            runner=_runner(),
            timeout_seconds=0.1,
        ) as locked:
            _write_private_file(
                tmp_path / "codex-home",
                "config.toml",
                b"# changed while locked\n",
            )
            with pytest.raises(ForgeError) as changed:
                locked.revalidate()
            assert changed.value.code == "transaction.plan_changed"
            assert not locked.closed
    finally:
        store.close()
        proof.close()
        owned.close()


def test_locked_recovery_rejects_untrusted_runner_before_lock_creation(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError, RunnerState
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.recovery import (
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
    lock_path = tmp_path / "codex-home" / "plugins" / ".zagrosi" / "install.lock"
    try:
        current = observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = plan_recovery(
            observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        runner = replace(_runner(), state=RunnerState.UNVERIFIED_SELF_ROOT)

        with pytest.raises(ForgeError) as untrusted:
            lock_recovery_plan(
                plan,
                authority=authority,
                owned_root=owned,
                runner=runner,
                timeout_seconds=0.1,
            )
        assert untrusted.value.code == "runner.untrusted"
        assert not lock_path.exists()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_locked_recovery_close_cannot_release_during_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threading import Event, Thread

    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    locked = None
    release_observation = Event()
    observation_started = Event()
    close_finished = Event()
    validation_errors: list[BaseException] = []
    real_observe = recovery.observe_recovery_snapshot
    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = recovery.plan_recovery(
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        locked = recovery.lock_recovery_plan(
            plan,
            authority=authority,
            owned_root=owned,
            runner=_runner(),
            timeout_seconds=0.1,
        )

        def blocking_observation(*args, **kwargs):
            observation_started.set()
            if not release_observation.wait(2):
                raise AssertionError("revalidation observation was not released")
            return real_observe(*args, **kwargs)

        def revalidate() -> None:
            try:
                locked.revalidate()
            except BaseException as exc:
                validation_errors.append(exc)

        def close() -> None:
            locked.close()
            close_finished.set()

        monkeypatch.setattr(
            recovery,
            "observe_recovery_snapshot",
            blocking_observation,
        )
        validation = Thread(target=revalidate)
        closing = Thread(target=close)
        validation.start()
        assert observation_started.wait(2)
        closing.start()
        released_early = close_finished.wait(0.1)
        release_observation.set()
        validation.join(2)
        closing.join(2)

        assert not released_early
        assert not validation.is_alive()
        assert not closing.is_alive()
        assert validation_errors == []
        assert close_finished.is_set()
        assert locked.closed
    finally:
        release_observation.set()
        if locked is not None:
            locked.close()
        store.close()
        proof.close()
        owned.close()


def test_concurrent_locked_recovery_close_waits_for_kernel_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threading import Event, Thread

    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    acquired = []
    release_started = Event()
    release_allowed = Event()
    second_returned = Event()
    first_errors: list[BaseException] = []
    second_errors: list[BaseException] = []
    real_acquire = recovery.acquire_install_lock
    real_release = recovery.HeldInstallLock.release

    def record_acquire(*args, **kwargs):
        held = real_acquire(*args, **kwargs)
        acquired.append(held)
        return held

    def blocking_release(held) -> None:
        if held is acquired[-1]:
            release_started.set()
            if not release_allowed.wait(2):
                raise AssertionError("kernel release was not allowed")
        real_release(held)

    locked = None
    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = recovery.plan_recovery(
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        monkeypatch.setattr(recovery, "acquire_install_lock", record_acquire)
        locked = recovery.lock_recovery_plan(
            plan,
            authority=authority,
            owned_root=owned,
            runner=_runner(),
            timeout_seconds=0.1,
        )
        monkeypatch.setattr(recovery.HeldInstallLock, "release", blocking_release)

        def first_close() -> None:
            try:
                locked.close()
            except BaseException as exc:
                first_errors.append(exc)

        def second_close() -> None:
            try:
                locked.close()
            except BaseException as exc:
                second_errors.append(exc)
            finally:
                second_returned.set()

        first = Thread(target=first_close)
        second = Thread(target=second_close)
        first.start()
        assert release_started.wait(2)
        second.start()
        returned_before_release = second_returned.wait(0.1)
        release_allowed.set()
        first.join(2)
        second.join(2)

        assert not returned_before_release
        assert not first.is_alive()
        assert not second.is_alive()
        assert first_errors == []
        assert second_errors == []
        assert acquired[-1]._released
        assert locked.closed
    finally:
        release_allowed.set()
        if acquired and not acquired[-1]._released:
            real_release(acquired[-1])
        store.close()
        proof.close()
        owned.close()


def test_locked_recovery_close_retries_preconsumption_release_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.lock import acquire_install_lock
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    release_attempts = 0
    observed_locks = []
    real_release = recovery.HeldInstallLock.release
    locked = None
    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = recovery.plan_recovery(
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        locked = recovery.lock_recovery_plan(
            plan,
            authority=authority,
            owned_root=owned,
            runner=_runner(),
            timeout_seconds=0.1,
        )

        def fail_once_before_consumption(held) -> None:
            nonlocal release_attempts
            release_attempts += 1
            observed_locks.append(held)
            if release_attempts == 1:
                raise RuntimeError("injected pre-consumption release failure")
            real_release(held)

        monkeypatch.setattr(
            recovery.HeldInstallLock,
            "release",
            fail_once_before_consumption,
        )
        with pytest.raises(RuntimeError, match="pre-consumption"):
            locked.close()
        assert not observed_locks[0]._released
        assert not locked.closed

        locked.close()
        assert release_attempts == 2
        assert observed_locks[0]._released
        assert locked.closed
        with acquire_install_lock(owned, timeout_seconds=0.1):
            pass
    finally:
        if locked is not None:
            locked.close()
        store.close()
        proof.close()
        owned.close()


def test_abandoned_locked_recovery_releases_kernel_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gc
    from weakref import ref

    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.lock import acquire_install_lock
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    acquired = []
    real_acquire = recovery.acquire_install_lock

    def record_acquire(*args, **kwargs):
        held = real_acquire(*args, **kwargs)
        acquired.append(held)
        return held

    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = recovery.plan_recovery(
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        monkeypatch.setattr(recovery, "acquire_install_lock", record_acquire)
        locked = recovery.lock_recovery_plan(
            plan,
            authority=authority,
            owned_root=owned,
            runner=_runner(),
            timeout_seconds=0.1,
        )
        abandoned = ref(locked)
        initial_lock = acquired[0]

        del locked
        gc.collect()

        assert abandoned() is None
        assert initial_lock._released
        with acquire_install_lock(owned, timeout_seconds=0.1):
            pass
    finally:
        store.close()
        proof.close()
        owned.close()


def test_abandoned_locked_recovery_retains_failed_release_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gc
    from weakref import ref

    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    acquired = []
    release_attempts = 0
    real_acquire = recovery.acquire_install_lock
    real_release = recovery.HeldInstallLock.release
    initial_lock = None

    def record_acquire(*args, **kwargs):
        held = real_acquire(*args, **kwargs)
        acquired.append(held)
        return held

    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = recovery.plan_recovery(
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        monkeypatch.setattr(recovery, "acquire_install_lock", record_acquire)
        locked = recovery.lock_recovery_plan(
            plan,
            authority=authority,
            owned_root=owned,
            runner=_runner(),
            timeout_seconds=0.1,
        )
        abandoned = ref(locked)
        initial_lock = acquired[0]

        def fail_twice_before_consumption(held) -> None:
            nonlocal release_attempts
            if held is initial_lock:
                release_attempts += 1
                if release_attempts <= 2:
                    raise RuntimeError("injected abandoned release failure")
            real_release(held)

        monkeypatch.setattr(
            recovery.HeldInstallLock,
            "release",
            fail_twice_before_consumption,
        )
        del locked
        gc.collect()

        assert abandoned() is None
        assert release_attempts == 2
        assert not initial_lock._released
        assert any(
            lease._held_lock is initial_lock
            for lease in recovery._ABANDONED_LOCKED_RECOVERY_LEASES
        )

        monkeypatch.setattr(recovery.HeldInstallLock, "release", real_release)
        with recovery.lock_recovery_plan(
            plan,
            authority=authority,
            owned_root=owned,
            runner=_runner(),
            timeout_seconds=0.1,
        ):
            assert initial_lock._released
        assert not recovery._ABANDONED_LOCKED_RECOVERY_LEASES
    finally:
        monkeypatch.setattr(recovery.HeldInstallLock, "release", real_release)
        if initial_lock is not None and not initial_lock._released:
            real_release(initial_lock)
        recovery._retry_abandoned_locked_leases()
        store.close()
        proof.close()
        owned.close()


def test_locked_recovery_rejects_critical_field_deletion_and_substitution(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    locked = None
    retained_lock = None
    original_plan = None
    try:
        current = recovery.observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = recovery.plan_recovery(
            recovery.observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )
        locked = recovery.lock_recovery_plan(
            plan,
            authority=authority,
            owned_root=owned,
            runner=_runner(),
            timeout_seconds=0.1,
        )
        retained_lock = getattr(locked, "_held_lock", None)
        original_plan = getattr(locked, "_plan", None)

        with pytest.raises(AttributeError):
            del locked._held_lock
        replacement = recovery.plan_recovery(
            recovery.RecoverySnapshot(
                journals=(),
                current_config=None,
            )
        )
        with pytest.raises(AttributeError):
            object.__setattr__(locked, "_plan", replacement)
    finally:
        if locked is not None:
            try:
                if original_plan is not None and hasattr(locked, "_plan"):
                    object.__setattr__(locked, "_plan", original_plan)
                locked.close()
            except (AttributeError, ForgeError):
                if retained_lock is not None and not retained_lock._released:
                    retained_lock.release()
        store.close()
        proof.close()
        owned.close()


def test_locked_recovery_rejects_mismatched_authority_before_lock_creation(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.paths import PlatformPathAuthority
    from zagrosi_forge.install.recovery import (
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
    lock_path = tmp_path / "codex-home" / "plugins" / ".zagrosi" / "install.lock"
    try:
        current = observe_current_config_identity(
            authority=authority,
            owned_root=owned,
        )
        prepared = _bind_prepared_to_current_config(_prepared(binding), current)
        store.create_prepared(prepared)
        plan = plan_recovery(
            observe_recovery_snapshot(
                authority=authority,
                owned_root=owned,
            )
        )

        with pytest.raises(TypeError, match="matching path authority"):
            lock_recovery_plan(
                plan,
                authority=PlatformPathAuthority(),
                owned_root=owned,
                runner=_runner(),
                timeout_seconds=0.1,
            )
        assert not lock_path.exists()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_persistent_transaction_quarantine_rename_is_durable_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    rebound = None
    path = None
    ticket = None
    events: list[str] = []
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        store.close()
        proof.close()

        rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=binding,
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

        if os.name == "nt":
            durable_rename = ownership._durable_windows_directory_rename

            def record_durable_rename(*args, **kwargs) -> None:
                durable_rename(*args, **kwargs)
                events.extend(("rename", "flush"))

            monkeypatch.setattr(
                ownership,
                "_durable_windows_directory_rename",
                record_durable_rename,
            )
        else:
            exclusive_rename = ownership._exclusive_rename
            fsync = ownership.os.fsync

            def record_rename(*args, **kwargs) -> None:
                exclusive_rename(*args, **kwargs)
                events.append("rename")

            def record_fsync(descriptor: int) -> None:
                fsync(descriptor)
                events.append("flush")

            monkeypatch.setattr(ownership, "_exclusive_rename", record_rename)
            monkeypatch.setattr(ownership.os, "fsync", record_fsync)

        ticket = ownership.quarantine_owned(
            cleanup,
            transaction_id=prepared.transaction_id,
        ).unwrap()

        assert events == ["rename", "flush"]
        assert ticket.recovery_reference == binding.quarantine_relative
    finally:
        if ticket is not None:
            ticket.close()
        if path is not None:
            path.close()
        if rebound is not None:
            rebound.close()
        store.close()
        proof.close()
        owned.close()


def test_quarantine_flush_failure_retains_exact_recovery_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    rebound = None
    path = None
    try:
        prepared = _prepared(binding)
        store.create_prepared(prepared)
        store.close()
        proof.close()

        rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=binding,
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

        def fail_flush(*_args: object) -> None:
            raise OSError("injected quarantine flush failure")

        if os.name == "nt":
            monkeypatch.setattr(
                ownership,
                "_windows_flush_directory_binding",
                fail_flush,
            )
        else:
            monkeypatch.setattr(ownership.os, "fsync", fail_flush)

        result = ownership.quarantine_owned(
            cleanup,
            transaction_id=prepared.transaction_id,
        )

        assert not result.is_ok
        assert result.error is not None
        assert result.error.code == "ownership.quarantine_conflict"
        assert result.error.recovery_instructions == (binding.quarantine_relative,)
        assert (directory.parents[2] / binding.quarantine_relative).is_dir()
    finally:
        if path is not None:
            path.close()
        if rebound is not None:
            rebound.close()
        store.close()
        proof.close()
        owned.close()


def test_prepared_preserves_declared_rollback_order(tmp_path: Path) -> None:
    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, declared = _prepared_with_ordered_rollback(binding)

        assert prepared.rollback_actions == declared
        assert prepared.rollback_actions[-1].relative_path == binding.root_relative
        assert prepared.rollback_actions[-1].action == "quarantine-if-owned"
        assert not tuple(directory.glob("journal*"))
    finally:
        store.close()
        proof.close()
        owned.close()


def test_prepared_rejects_transaction_root_quarantine_before_final_action(
    tmp_path: Path,
) -> None:
    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, declared = _prepared_with_ordered_rollback(binding)

        with pytest.raises(ValueError, match="persistent transaction rollback"):
            replace(
                prepared,
                rollback_actions=(declared[-1],) + declared[:-1],
            )

        assert not tuple(directory.glob("journal*"))
    finally:
        store.close()
        proof.close()
        owned.close()


def test_prepared_rejects_non_root_quarantine_rollback_action(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import RollbackAction, TransactionOwnedPath

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        candidate = TransactionOwnedPath(
            role="candidate-stage",
            relative_path=f"{binding.root_relative}/candidate-stage",
            expected_identity=(binding.transaction_identity[0], 987654),
        )
        action = RollbackAction(
            action="quarantine-if-owned",
            relative_path=candidate.relative_path,
            expected_identity=candidate.expected_identity,
        )

        with pytest.raises(ValueError, match="persistent transaction rollback"):
            replace(
                prepared,
                transaction_owned_paths=(
                    *prepared.transaction_owned_paths,
                    candidate,
                ),
                rollback_actions=(action, prepared.rollback_actions[-1]),
            )

        assert not tuple(directory.glob("journal*"))
    finally:
        store.close()
        proof.close()
        owned.close()


def test_rollback_completion_requires_matching_durable_intent(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, _declared = _prepared_with_ordered_rollback(binding)
        head = store.create_prepared(prepared)
        action = prepared.rollback_actions[0]

        with pytest.raises(ForgeError) as raised:
            store.append(
                head,
                JournalTransition(
                    JournalState.ROLLBACK_ACTION_COMPLETED,
                    rollback_event=_rollback_completion(action, 0, binding),
                ),
            )

        assert raised.value.code == "journal.corrupt"
        assert not (directory / "journal-00000001.json").exists()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_live_journal_cannot_claim_final_root_quarantine_completion(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        root_action = prepared.rollback_actions[-1]
        root_index = len(prepared.rollback_actions) - 1
        head = store.create_prepared(prepared)
        head = store.append(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_INTENT,
                rollback_event=_rollback_intent(root_action, root_index),
            ),
        )
        before = tuple(
            (path.name, path.read_bytes())
            for path in sorted(directory.glob("journal*"))
        )

        with pytest.raises(ForgeError) as raised:
            store.append(
                head,
                JournalTransition(
                    JournalState.ROLLBACK_ACTION_COMPLETED,
                    rollback_event=_rollback_completion(
                        root_action,
                        root_index,
                        binding,
                    ),
                ),
            )

        assert raised.value.code == "journal.corrupt"
        assert (
            tuple(
                (path.name, path.read_bytes())
                for path in sorted(directory.glob("journal*"))
            )
            == before
        )
    finally:
        store.close()
        proof.close()
        owned.close()


def test_live_private_append_cannot_enable_quarantined_recovery(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        root_action = prepared.rollback_actions[-1]
        root_index = len(prepared.rollback_actions) - 1
        head = store.create_prepared(prepared)
        head = store.append(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_INTENT,
                rollback_event=_rollback_intent(root_action, root_index),
            ),
        )
        before = tuple(
            (path.name, path.read_bytes())
            for path in sorted(directory.glob("journal*"))
        )

        with pytest.raises(TypeError):
            store._append(
                head,
                JournalTransition(
                    JournalState.ROLLBACK_ACTION_COMPLETED,
                    rollback_event=_rollback_completion(
                        root_action,
                        root_index,
                        binding,
                    ),
                ),
                quarantined_recovery=True,  # type: ignore[call-arg]
            )

        assert (
            tuple(
                (path.name, path.read_bytes())
                for path in sorted(directory.glob("journal*"))
            )
            == before
        )
    finally:
        store.close()
        proof.close()
        owned.close()


def test_live_journal_rejects_resealed_final_root_completion(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.journal as journal
    from zagrosi_forge.install.contracts import ForgeError

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        root_action = prepared.rollback_actions[-1]
        root_index = len(prepared.rollback_actions) - 1
        head = store.create_prepared(prepared)
        store.append(
            head,
            journal.JournalTransition(
                journal.JournalState.ROLLBACK_ACTION_INTENT,
                rollback_event=_rollback_intent(root_action, root_index),
            ),
        )
        intent = json.loads((directory / "journal-00000001.json").read_bytes())
        completion = {
            key: value for key, value in intent.items() if key != "record_digest"
        }
        completion.update(
            {
                "previous_record_digest": intent["record_digest"],
                "rollback_event": journal._rollback_event_projection(
                    _rollback_completion(
                        root_action,
                        root_index,
                        binding,
                    )
                ),
                "sequence": 2,
                "state": journal.JournalState.ROLLBACK_ACTION_COMPLETED.value,
            }
        )
        _write_private_file(
            directory,
            "journal-00000002.json",
            journal._seal_record(completion),
        )
        before = tuple(
            (path.name, path.read_bytes())
            for path in sorted(directory.glob("journal*"))
        )

        with pytest.raises(ForgeError) as raised:
            store.load()

        assert raised.value.code == "journal.corrupt"
        assert (
            tuple(
                (path.name, path.read_bytes())
                for path in sorted(directory.glob("journal*"))
            )
            == before
        )
    finally:
        store.close()
        proof.close()
        owned.close()


def test_canonical_quarantine_requires_durable_root_intent(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalStore
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    live_rebound = None
    path = None
    quarantine_ticket = None
    quarantine_rebound = None
    quarantined_store = None
    access = None
    try:
        store.create_prepared(_prepared(binding))
        store.close()
        proof.close()

        live_rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=binding,
        ).unwrap()
        assert live_rebound.claim is not None
        path = authority.prove_descendant(
            owned,
            live_rebound.claim.relative,
            expected_depth=3,
        ).unwrap()
        cleanup = ownership.prove_transaction_owned(
            path,
            claim=live_rebound.claim,
        ).unwrap()
        quarantine_ticket = ownership.quarantine_owned(
            cleanup,
            transaction_id=binding.transaction_id,
        ).unwrap()
        quarantine_rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=binding,
        ).unwrap()
        access = ownership.open_transaction_journal_access(
            owned,
            quarantine_rebound,
        ).unwrap()
        quarantined_store = JournalStore(access)
        access = None

        with pytest.raises(ForgeError) as raised:
            quarantined_store.load()

        assert raised.value.code == "journal.corrupt"
    finally:
        if quarantined_store is not None:
            quarantined_store.close()
        elif access is not None:
            access.close()
        if path is not None:
            path.close()
        if live_rebound is not None:
            live_rebound.close()
        if quarantine_rebound is not None:
            quarantine_rebound.close()
        if quarantine_ticket is not None:
            quarantine_ticket.close()
        store.close()
        proof.close()
        owned.close()


@pytest.mark.parametrize(
    "corruption",
    ("gap", "duplicate", "wrong-digest", "reordered-action"),
)
def test_rollback_wal_rejects_noncontiguous_or_unbound_progress(
    tmp_path: Path,
    corruption: str,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import (
        JournalRollbackEvent,
        JournalState,
        JournalTransition,
    )

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, _declared = _prepared_with_ordered_rollback(binding)
        head = store.create_prepared(prepared)
        actions = prepared.rollback_actions

        if corruption == "duplicate":
            head = _append_rollback_pair(store, head, actions[0], 0, binding)
            event = _rollback_completion(actions[0], 0, binding)
            state = JournalState.ROLLBACK_ACTION_COMPLETED
            rejected_sequence = 3
        elif corruption == "gap":
            event = _rollback_intent(actions[1], 1)
            state = JournalState.ROLLBACK_ACTION_INTENT
            rejected_sequence = 1
        elif corruption == "reordered-action":
            event = _rollback_intent(actions[1], 0)
            state = JournalState.ROLLBACK_ACTION_INTENT
            rejected_sequence = 1
        else:
            action_digest = actions[0].action_digest
            wrong_digest = ("0" if action_digest[0] != "0" else "1") + action_digest[1:]
            event = JournalRollbackEvent(
                action_index=0,
                action_digest=wrong_digest,
            )
            state = JournalState.ROLLBACK_ACTION_INTENT
            rejected_sequence = 1

        with pytest.raises(ForgeError) as raised:
            store.append(
                head,
                JournalTransition(state, rollback_event=event),
            )

        assert raised.value.code == "journal.corrupt"
        assert not (directory / f"journal-{rejected_sequence:08d}.json").exists()
    finally:
        store.close()
        proof.close()
        owned.close()


@pytest.mark.parametrize(
    ("changed", "value"),
    (
        ("outcome", "retained"),
        ("observed_identity", (91, 92)),
        ("recovery_reference", ".zagrosi/quarantine/forged"),
    ),
)
def test_rollback_intent_rejects_completion_evidence(
    tmp_path: Path,
    changed: str,
    value: object,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import (
        JournalRollbackEvent,
        JournalState,
        JournalTransition,
    )

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, _declared = _prepared_with_ordered_rollback(binding)
        head = store.create_prepared(prepared)
        action = prepared.rollback_actions[0]
        arguments = {
            "action_index": 0,
            "action_digest": action.action_digest,
            changed: value,
        }
        event = JournalRollbackEvent(**arguments)

        with pytest.raises(ForgeError) as raised:
            store.append(
                head,
                JournalTransition(
                    JournalState.ROLLBACK_ACTION_INTENT,
                    rollback_event=event,
                ),
            )

        assert raised.value.code == "journal.corrupt"
        assert not (directory / "journal-00000001.json").exists()
    finally:
        store.close()
        proof.close()
        owned.close()


def test_rolled_back_requires_every_declared_action_completion(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import JournalState, JournalTransition

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, _declared = _prepared_with_ordered_rollback(binding)
        head = store.create_prepared(prepared)
        head = _append_rollback_pair(
            store,
            head,
            prepared.rollback_actions[0],
            0,
            binding,
        )

        with pytest.raises(ForgeError) as raised:
            store.append(head, JournalTransition(JournalState.ROLLED_BACK))
        assert raised.value.code == "journal.corrupt"
        assert not (directory / "journal-00000003.json").exists()

        root_index = len(prepared.rollback_actions) - 1
        root_action = prepared.rollback_actions[root_index]
        head = store.append(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_INTENT,
                rollback_event=_rollback_intent(root_action, root_index),
            ),
        )
        store.close()
        quarantine_ticket, recovery_store = _open_recovery_store_after_quarantine(
            owned,
            proof,
            binding,
        )
        head = recovery_store.append_recovery(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_COMPLETED,
                rollback_event=_rollback_completion(
                    root_action,
                    root_index,
                    binding,
                ),
            ),
        )
        head = recovery_store.append_recovery(
            head,
            JournalTransition(JournalState.ROLLED_BACK),
        )

        assert head.state is JournalState.ROLLED_BACK
        assert head.sequence == 2 * len(prepared.rollback_actions) + 1
    finally:
        if recovery_store is not None:
            recovery_store.close()
        if quarantine_ticket is not None:
            quarantine_ticket.close()
        store.close()
        proof.close()
        owned.close()


def test_recovery_plan_resumes_at_first_incomplete_rollback_action(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import JournalState, JournalTransition
    from zagrosi_forge.install.recovery import (
        RecoveryDisposition,
        RecoverySnapshot,
        plan_recovery,
    )

    store, proof, owned, _directory, binding = _store(tmp_path)
    quarantine_ticket = None
    recovery_store = None
    try:
        prepared, _declared = _prepared_with_ordered_rollback(binding)
        head = store.create_prepared(prepared)
        head = _append_rollback_pair(
            store,
            head,
            prepared.rollback_actions[0],
            0,
            binding,
        )

        plan = plan_recovery(
            RecoverySnapshot(
                journals=(store.load(),),
                current_config=prepared.before_config,
            )
        )

        assert plan.disposition is RecoveryDisposition.ROLLBACK_CANDIDATE
        assert plan.rollback_actions == prepared.rollback_actions
        assert plan.next_action_index == 1

        root_index = len(prepared.rollback_actions) - 1
        root_action = prepared.rollback_actions[root_index]
        head = store.append(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_INTENT,
                rollback_event=_rollback_intent(root_action, root_index),
            ),
        )
        store.close()
        quarantine_ticket, recovery_store = _open_recovery_store_after_quarantine(
            owned,
            proof,
            binding,
        )
        recovery_store.append_recovery(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_COMPLETED,
                rollback_event=_rollback_completion(
                    root_action,
                    root_index,
                    binding,
                ),
            ),
        )
        completed_plan = plan_recovery(
            RecoverySnapshot(
                journals=(recovery_store.load(),),
                current_config=prepared.before_config,
            )
        )

        assert completed_plan.disposition is RecoveryDisposition.ROLLBACK_CANDIDATE
        assert completed_plan.rollback_actions == prepared.rollback_actions
        assert completed_plan.next_action_index == len(prepared.rollback_actions)
    finally:
        if recovery_store is not None:
            recovery_store.close()
        if quarantine_ticket is not None:
            quarantine_ticket.close()
        store.close()
        proof.close()
        owned.close()


def test_completed_rollback_record_validates_against_closed_schema(
    tmp_path: Path,
) -> None:
    from jsonschema import Draft202012Validator

    from zagrosi_forge.install.journal import JournalState

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, _declared = _prepared_with_ordered_rollback(binding)
        head = store.create_prepared(prepared)
        _append_rollback_pair(
            store,
            head,
            prepared.rollback_actions[0],
            0,
            binding,
        )
        record = json.loads((directory / "journal-00000002.json").read_bytes())
        schema_path = (
            Path(__file__).parents[2]
            / "src/zagrosi_forge/install/schemas/transaction-journal-v1.schema.json"
        )
        validator = Draft202012Validator(json.loads(schema_path.read_bytes()))

        validator.validate(record)
        assert record["state"] == JournalState.ROLLBACK_ACTION_COMPLETED.value
        assert record["rollback_event"] == {
            "action_digest": prepared.rollback_actions[0].action_digest,
            "action_index": 0,
            "observed_identity": None,
            "outcome": "retained",
            "recovery_reference": None,
        }
    finally:
        store.close()
        proof.close()
        owned.close()


def test_repeated_rollback_events_load_beyond_state_enum_count(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.journal import JournalState, JournalTransition
    from zagrosi_forge.install.policies import LIMIT_POLICY

    store, proof, owned, _directory, binding = _store(tmp_path)
    quarantine_ticket = None
    recovery_store = None
    try:
        prepared, _declared = _prepared_with_rollback_action_count(binding, 8)
        head = store.create_prepared(prepared)
        for index, action in enumerate(prepared.rollback_actions[:-1]):
            head = _append_rollback_pair(store, head, action, index, binding)

        root_index = len(prepared.rollback_actions) - 1
        root_action = prepared.rollback_actions[root_index]
        head = store.append(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_INTENT,
                rollback_event=_rollback_intent(root_action, root_index),
            ),
        )
        store.close()
        quarantine_ticket, recovery_store = _open_recovery_store_after_quarantine(
            owned,
            proof,
            binding,
        )
        head = recovery_store.append_recovery(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_COMPLETED,
                rollback_event=_rollback_completion(
                    root_action,
                    root_index,
                    binding,
                ),
            ),
        )
        loaded = recovery_store.load()

        assert len(loaded.records) == 1 + 2 * len(prepared.rollback_actions)
        assert len(loaded.records) > len(JournalState)
        assert len(loaded.records) < LIMIT_POLICY.value("journal_records")
        assert tuple(record.sequence for record in loaded.records) == tuple(
            range(len(loaded.records))
        )
        assert loaded.head == head
    finally:
        if recovery_store is not None:
            recovery_store.close()
        if quarantine_ticket is not None:
            quarantine_ticket.close()
        store.close()
        proof.close()
        owned.close()


def test_rollback_action_limit_matches_closed_schema(tmp_path: Path) -> None:
    from jsonschema import Draft202012Validator, ValidationError

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, _declared = _prepared_with_rollback_action_count(binding, 12)
        store.create_prepared(prepared)
        record = json.loads((directory / "journal-00000000.json").read_bytes())
        schema_path = (
            Path(__file__).parents[2]
            / "src/zagrosi_forge/install/schemas/transaction-journal-v1.schema.json"
        )
        schema = json.loads(schema_path.read_bytes())
        validator = Draft202012Validator(schema)

        assert schema["properties"]["rollback_actions"]["maxItems"] == 12
        validator.validate(record)
        with pytest.raises(ValueError, match="prepared transaction paths"):
            _prepared_with_rollback_action_count(binding, 13)

        oversized = {
            **record,
            "rollback_actions": record["rollback_actions"]
            + [record["rollback_actions"][0]],
        }
        with pytest.raises(ValidationError):
            validator.validate(oversized)
    finally:
        store.close()
        proof.close()
        owned.close()


def test_resealed_rollback_event_tamper_preserves_canonical_chain(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, _declared = _prepared_with_ordered_rollback(binding)
        head = store.create_prepared(prepared)
        _append_rollback_pair(
            store,
            head,
            prepared.rollback_actions[0],
            0,
            binding,
        )
        completed = directory / "journal-00000002.json"

        def tamper(value):
            event = {
                **value["rollback_event"],
                "action_digest": prepared.rollback_actions[1].action_digest,
            }
            return {**value, "rollback_event": event}

        _rewrite(completed, tamper)
        retained = tuple(
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
            == retained
        )
    finally:
        store.close()
        proof.close()
        owned.close()


def test_prepared_rejects_portable_rollback_path_collision(tmp_path: Path) -> None:
    from zagrosi_forge.install.journal import RollbackAction, TransactionOwnedPath

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared = _prepared(binding)
        colliding = tuple(
            TransactionOwnedPath(
                role=f"collision-{index}",
                relative_path=relative,
                expected_identity=None,
            )
            for index, relative in enumerate(("stages/Candidate", "stages/candidate"))
        )
        actions = tuple(
            RollbackAction(
                action="retain",
                relative_path=item.relative_path,
                expected_identity=None,
            )
            for item in colliding
        )

        with pytest.raises(ValueError, match="prepared transaction paths"):
            replace(
                prepared,
                transaction_owned_paths=(
                    *prepared.transaction_owned_paths,
                    *colliding,
                ),
                rollback_actions=(
                    *actions,
                    prepared.rollback_actions[-1],
                ),
            )

        assert not tuple(directory.glob("journal*"))
    finally:
        store.close()
        proof.close()
        owned.close()


def _quarantine_after_root_rollback_intent(tmp_path: Path):
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.journal import JournalState, JournalTransition
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    live_rebound = None
    path = None
    quarantine_ticket = None
    quarantine_rebound = None
    try:
        prepared = _prepared(binding)
        root_action = prepared.rollback_actions[-1]
        root_index = len(prepared.rollback_actions) - 1
        head = store.create_prepared(prepared)
        head = store.append(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_INTENT,
                rollback_event=_rollback_intent(root_action, root_index),
            ),
        )
        store.close()
        proof.close()

        live_rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=binding,
        ).unwrap()
        assert live_rebound.claim is not None
        path = authority.prove_descendant(
            owned,
            live_rebound.claim.relative,
            expected_depth=3,
        ).unwrap()
        cleanup = ownership.prove_transaction_owned(
            path,
            claim=live_rebound.claim,
        ).unwrap()
        quarantine_ticket = ownership.quarantine_owned(
            cleanup,
            transaction_id=prepared.transaction_id,
        ).unwrap()
        quarantine_rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=binding,
        ).unwrap()

        return (
            prepared,
            head,
            owned,
            binding,
            quarantine_ticket,
            quarantine_rebound,
        )
    except BaseException:
        if quarantine_rebound is not None:
            quarantine_rebound.close()
        if quarantine_ticket is not None:
            quarantine_ticket.close()
        owned.close()
        raise
    finally:
        if path is not None:
            path.close()
        if live_rebound is not None:
            live_rebound.close()
        store.close()
        proof.close()


def _open_recovery_store_after_quarantine(
    owned,
    proof,
    binding,
):
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.journal import JournalStore

    live_rebound = None
    quarantine_ticket = None
    quarantine_rebound = None
    access = None
    recovery_store = None
    try:
        live_rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=binding,
        ).unwrap()
        assert live_rebound.claim is not None
        cleanup = ownership.prove_transaction_owned(
            proof,
            claim=live_rebound.claim,
        ).unwrap()
        quarantine_ticket = ownership.quarantine_owned(
            cleanup,
            transaction_id=binding.transaction_id,
        ).unwrap()
        quarantine_rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=binding,
        ).unwrap()
        access = ownership.open_quarantined_recovery_journal_access(
            owned,
            quarantine_rebound,
        ).unwrap()
        recovery_store = JournalStore.from_quarantined_recovery(access)
        access = None
        quarantine_rebound.close()
        quarantine_rebound = None
        return quarantine_ticket, recovery_store
    except BaseException:
        if recovery_store is not None:
            recovery_store.close()
        elif access is not None:
            access.close()
        if quarantine_ticket is not None:
            quarantine_ticket.close()
        raise
    finally:
        if quarantine_rebound is not None:
            quarantine_rebound.close()
        if live_rebound is not None:
            live_rebound.close()


def test_quarantined_recovery_journal_completes_exact_root_action_then_rollback(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.journal import (
        JournalState,
        JournalStore,
        JournalTransition,
    )

    (
        prepared,
        head,
        owned,
        binding,
        quarantine_ticket,
        quarantine_rebound,
    ) = _quarantine_after_root_rollback_intent(tmp_path)
    access = None
    recovery_store = None
    try:
        access = ownership.open_quarantined_recovery_journal_access(
            owned,
            quarantine_rebound,
        ).unwrap()
        recovery_store = JournalStore.from_quarantined_recovery(access)
        access = None
        root_action = prepared.rollback_actions[-1]
        root_index = len(prepared.rollback_actions) - 1

        head = recovery_store.append_recovery(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_COMPLETED,
                rollback_event=_rollback_completion(
                    root_action,
                    root_index,
                    binding,
                ),
            ),
        )
        head = recovery_store.append_recovery(
            head,
            JournalTransition(JournalState.ROLLED_BACK),
        )

        loaded = recovery_store.load()
        assert head.state is JournalState.ROLLED_BACK
        assert loaded.head == head
        assert tuple(record.state for record in loaded.records[-3:]) == (
            JournalState.ROLLBACK_ACTION_INTENT,
            JournalState.ROLLBACK_ACTION_COMPLETED,
            JournalState.ROLLED_BACK,
        )
    finally:
        if recovery_store is not None:
            recovery_store.close()
        elif access is not None:
            access.close()
        quarantine_rebound.close()
        quarantine_ticket.close()
        owned.close()


def test_quarantined_recovery_access_consumes_source_ticket_once(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    (
        _prepared,
        _head,
        owned,
        _binding,
        quarantine_ticket,
        quarantine_rebound,
    ) = _quarantine_after_root_rollback_intent(tmp_path)
    first_access = None
    unexpected_second_access = None
    try:
        first_access = ownership.open_quarantined_recovery_journal_access(
            owned,
            quarantine_rebound,
        ).unwrap()
        second = ownership.open_quarantined_recovery_journal_access(
            owned,
            quarantine_rebound,
        )
        if second.is_ok:
            unexpected_second_access = second.unwrap()

        assert not second.is_ok
        assert second.error is not None
        assert second.error.code == "ownership.unowned"
    finally:
        if unexpected_second_access is not None:
            unexpected_second_access.close()
        if first_access is not None:
            first_access.close()
        quarantine_rebound.close()
        quarantine_ticket.close()
        owned.close()


def test_transferred_recovery_store_survives_source_rebound_close(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.journal import (
        JournalState,
        JournalStore,
        JournalTransition,
    )

    (
        prepared,
        head,
        owned,
        binding,
        quarantine_ticket,
        quarantine_rebound,
    ) = _quarantine_after_root_rollback_intent(tmp_path)
    access = None
    recovery_store = None
    later_rebound = None
    try:
        access = ownership.open_quarantined_recovery_journal_access(
            owned,
            quarantine_rebound,
        ).unwrap()
        recovery_store = JournalStore.from_quarantined_recovery(access)
        access = None
        quarantine_rebound.close()
        root_action = prepared.rollback_actions[-1]
        root_index = len(prepared.rollback_actions) - 1

        head = recovery_store.append_recovery(
            head,
            JournalTransition(
                JournalState.ROLLBACK_ACTION_COMPLETED,
                rollback_event=_rollback_completion(
                    root_action,
                    root_index,
                    binding,
                ),
            ),
        )
        head = recovery_store.append_recovery(
            head,
            JournalTransition(JournalState.ROLLED_BACK),
        )
        assert head.state is JournalState.ROLLED_BACK
        recovery_store.close()
        recovery_store = None

        later_rebound = ownership.rebind_persistent_transaction(
            owned,
            binding=binding,
        ).unwrap()
        assert later_rebound.location is ownership.TransactionLocation.QUARANTINED
        assert later_rebound.ticket is not None
    finally:
        if later_rebound is not None:
            later_rebound.close()
        if recovery_store is not None:
            recovery_store.close()
        elif access is not None:
            access.close()
        quarantine_rebound.close()
        quarantine_ticket.close()
        owned.close()


@pytest.mark.parametrize("cleanup_state", ("intent", "complete"))
def test_quarantined_recovery_access_rejects_started_cleanup(
    tmp_path: Path,
    cleanup_state: str,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    (
        _prepared,
        _head,
        owned,
        binding,
        quarantine_ticket,
        quarantine_rebound,
    ) = _quarantine_after_root_rollback_intent(tmp_path)
    transaction_store = None
    unexpected_access = None
    try:
        transaction_store = ownership._open_transaction_store(
            owned,
            create=False,
        )
        intent = ownership._publish_transaction_cleanup_intent(
            transaction_store,
            binding,
        )
        if cleanup_state == "complete":
            ownership._publish_transaction_cleanup_complete(
                transaction_store,
                binding,
                delete_component=intent.delete_component,
            )
        transaction_store.close()
        transaction_store = None

        result = ownership.open_quarantined_recovery_journal_access(
            owned,
            quarantine_rebound,
        )
        if result.is_ok:
            unexpected_access = result.unwrap()

        assert not result.is_ok
        assert result.error is not None
        assert result.error.code == "ownership.unowned"
        assert result.error.recovery_instructions == (binding.quarantine_relative,)
    finally:
        if unexpected_access is not None:
            unexpected_access.close()
        if transaction_store is not None:
            transaction_store.close()
        quarantine_rebound.close()
        quarantine_ticket.close()
        owned.close()


def test_quarantined_recovery_access_rechecks_cleanup_after_ticket_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership

    (
        _prepared,
        _head,
        owned,
        binding,
        quarantine_ticket,
        quarantine_rebound,
    ) = _quarantine_after_root_rollback_intent(tmp_path)
    original_take = ownership.QuarantineTicket._take_authority
    published = False

    def take_then_publish_cleanup(selected):
        nonlocal published
        root, namespace = original_take(selected)
        transaction_store = ownership._open_transaction_store(
            owned,
            create=False,
        )
        try:
            ownership._publish_transaction_cleanup_intent(
                transaction_store,
                binding,
            )
            published = True
        finally:
            transaction_store.close()
        return root, namespace

    monkeypatch.setattr(
        ownership.QuarantineTicket,
        "_take_authority",
        take_then_publish_cleanup,
    )
    unexpected_access = None
    try:
        result = ownership.open_quarantined_recovery_journal_access(
            owned,
            quarantine_rebound,
        )
        if result.is_ok:
            unexpected_access = result.unwrap()

        assert published
        assert not result.is_ok
        assert result.error is not None
        assert result.error.code == "ownership.unowned"
        assert result.error.recovery_instructions == (binding.quarantine_relative,)
    finally:
        if unexpected_access is not None:
            unexpected_access.close()
        quarantine_rebound.close()
        quarantine_ticket.close()
        owned.close()


def test_quarantined_recovery_authority_rejects_every_other_mutation(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import (
        JournalState,
        JournalStore,
        JournalTransition,
    )

    (
        prepared,
        head,
        owned,
        binding,
        quarantine_ticket,
        quarantine_rebound,
    ) = _quarantine_after_root_rollback_intent(tmp_path)
    generic_access = None
    generic_store = None
    recovery_access = None
    recovery_store = None
    quarantined_directory = (
        tmp_path / "codex-home" / "plugins" / binding.quarantine_relative
    )
    root_action = prepared.rollback_actions[-1]
    root_index = len(prepared.rollback_actions) - 1
    exact_completion = JournalTransition(
        JournalState.ROLLBACK_ACTION_COMPLETED,
        rollback_event=_rollback_completion(
            root_action,
            root_index,
            binding,
        ),
    )
    try:
        before = tuple(
            (path.name, path.read_bytes())
            for path in sorted(quarantined_directory.glob("journal*"))
        )
        generic_access = ownership.open_transaction_journal_access(
            owned,
            quarantine_rebound,
        ).unwrap()
        generic_store = JournalStore(generic_access)
        generic_access = None
        with pytest.raises(ForgeError) as generic_error:
            generic_store.append(head, exact_completion)
        assert generic_error.value.code == "ownership.unowned"
        generic_store.close()
        generic_store = None

        recovery_access = ownership.open_quarantined_recovery_journal_access(
            owned,
            quarantine_rebound,
        ).unwrap()
        recovery_store = JournalStore.from_quarantined_recovery(recovery_access)
        recovery_access = None

        with pytest.raises(ForgeError) as create_error:
            recovery_store.create_prepared(prepared)
        assert create_error.value.code == "ownership.unowned"
        with pytest.raises(ForgeError) as append_error:
            recovery_store.append(head, exact_completion)
        assert append_error.value.code == "ownership.unowned"
        with pytest.raises(ForgeError) as direct_terminal_error:
            recovery_store.append_recovery(
                head,
                JournalTransition(JournalState.ROLLED_BACK),
            )
        assert direct_terminal_error.value.code == "journal.corrupt"
        with pytest.raises(ForgeError) as forward_error:
            recovery_store.append_recovery(
                head,
                JournalTransition(JournalState.STAGED),
            )
        assert forward_error.value.code == "journal.corrupt"
        wrong_completion = replace(
            exact_completion.rollback_event,
            recovery_reference=f"{binding.quarantine_relative}-wrong",
        )
        with pytest.raises(ForgeError) as completion_error:
            recovery_store.append_recovery(
                head,
                JournalTransition(
                    JournalState.ROLLBACK_ACTION_COMPLETED,
                    rollback_event=wrong_completion,
                ),
            )
        assert completion_error.value.code == "journal.corrupt"
        assert (
            tuple(
                (path.name, path.read_bytes())
                for path in sorted(quarantined_directory.glob("journal*"))
            )
            == before
        )
    finally:
        if recovery_store is not None:
            recovery_store.close()
        elif recovery_access is not None:
            recovery_access.close()
        if generic_store is not None:
            generic_store.close()
        elif generic_access is not None:
            generic_access.close()
        quarantine_rebound.close()
        quarantine_ticket.close()
        owned.close()


def test_prepared_reserves_exact_remaining_rollback_wal_before_publication(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.journal as journal
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.policies import LIMIT_POLICY

    store, proof, owned, directory, binding = _store(tmp_path)
    try:
        prepared, _declared = _prepared_with_rollback_action_count(binding, 12)
        baseline_record = journal._record_projection(
            prepared,
            transaction_binding=store._binding,
            state=journal.JournalState.PREPARED,
            sequence=0,
            previous_record_digest=journal._ZERO_DIGEST,
        )
        baseline_raw = journal._seal_record(baseline_record)
        record_limit = LIMIT_POLICY.value("journal_record_bytes")
        target_size = record_limit - 1024
        padding = (
            target_size - len(baseline_raw) + len(prepared.runner_provenance.origin)
        )
        prepared = replace(
            prepared,
            runner_provenance=replace(
                prepared.runner_provenance,
                origin="x" * padding,
            ),
        )
        prepared_record = journal._record_projection(
            prepared,
            transaction_binding=store._binding,
            state=journal.JournalState.PREPARED,
            sequence=0,
            previous_record_digest=journal._ZERO_DIGEST,
        )
        projected_records = [journal._seal_record(prepared_record)]

        def project(
            state: journal.JournalState,
            rollback_event=None,
        ) -> None:
            previous = json.loads(projected_records[-1])
            record = {
                key: value for key, value in previous.items() if key != "record_digest"
            }
            record.update(
                {
                    "previous_record_digest": previous["record_digest"],
                    "rollback_event": (
                        None
                        if rollback_event is None
                        else journal._rollback_event_projection(rollback_event)
                    ),
                    "sequence": previous["sequence"] + 1,
                    "state": state.value,
                }
            )
            projected_records.append(journal._seal_record(record))

        for index, action in enumerate(prepared.rollback_actions):
            project(
                journal.JournalState.ROLLBACK_ACTION_INTENT,
                _rollback_intent(action, index),
            )
            project(
                journal.JournalState.ROLLBACK_ACTION_COMPLETED,
                _rollback_completion(action, index, binding),
            )
        project(journal.JournalState.ROLLED_BACK)

        assert len(prepared.rollback_actions) == 12
        assert len(projected_records) == 1 + 2 * 12 + 1
        assert target_size <= len(projected_records[0]) < record_limit
        assert all(len(raw) <= record_limit for raw in projected_records)
        assert sum(map(len, projected_records)) > LIMIT_POLICY.value(
            "journal_total_bytes"
        )

        with pytest.raises(ForgeError) as raised:
            store.create_prepared(prepared)
        assert raised.value.code == "journal.limit_exceeded"
        assert not tuple(directory.glob("journal*"))
    finally:
        store.close()
        proof.close()
        owned.close()
