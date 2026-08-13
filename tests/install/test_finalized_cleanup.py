from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_transaction_recovery import (
    _advance_to_commit_intent,
    _completed_quarantined_rollback,
    _config_recovery_descriptor,
    _config_result,
    _finalizable_live_transaction,
    _prepared,
    _receipt_result,
    _store,
)


def _quarantine_transaction_root(
    authority,
    owned,
    proof,
    binding,
):
    import zagrosi_forge.install.ownership as ownership

    rebound = ownership.rebind_persistent_transaction(
        owned,
        binding=binding,
    ).unwrap()
    try:
        assert rebound.claim is not None
        cleanup_proof = ownership.prove_transaction_owned(
            proof,
            claim=rebound.claim,
        ).unwrap()
        try:
            return ownership.quarantine_owned(
                cleanup_proof,
                transaction_id=binding.transaction_id,
            ).unwrap()
        finally:
            cleanup_proof.close()
    finally:
        rebound.close()


def test_finalized_cleanup_authorization_requires_quarantine_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    import zagrosi_forge.install.recovery as recovery
    from zagrosi_forge.install.journal import (
        JournalState,
        JournalTransition,
    )

    authority, owned, store, proof, _current = _finalizable_live_transaction(
        tmp_path,
        state=JournalState.RECEIPT_COMMITTED,
    )
    ticket = None
    try:
        journal = store.load()
        prepared = journal.records[0].prepared
        assert prepared is not None
        source = next(
            item for item in prepared.identities if item.role == "source-generation"
        )
        source_manifest = (
            tmp_path
            / "codex-home/plugins"
            / source.relative_path
            / "plugins/zagrosi-forge/.codex-plugin/bundle-manifest.json"
        )
        original_manifest = source_manifest.read_bytes()
        store.append(
            journal.head,
            JournalTransition(JournalState.FINALIZED),
        )
        live_observation = ownership.discover_pending_transactions(owned).unwrap()[0]
        live_journal = store.load()

        live_authorization = ownership.authorize_recovery_cleanup(
            owned,
            observation=live_observation,
            journal=live_journal,
        )

        assert not live_authorization.is_ok
        claims = (
            tmp_path
            / "codex-home/plugins/.zagrosi/transactions/claims"
            / f"{prepared.transaction_id}.rc-auth.json"
        )
        assert not claims.exists()

        binding = live_observation.binding
        store.close()
        ticket = _quarantine_transaction_root(
            authority,
            owned,
            proof,
            binding,
        )
        ticket.close()

        snapshot = recovery.observe_recovery_snapshot(
            authority=authority,
            owned_root=owned,
        )
        observations = snapshot._observations
        journals = snapshot.journals
        finalizations = snapshot._finalization_observations

        assert observations is not None
        assert finalizations is not None
        assert snapshot.current_config is not None
        assert len(observations) == len(journals) == len(finalizations) == 1
        assert observations[0].location is ownership.TransactionLocation.QUARANTINED
        assert journals[0].head.state is JournalState.FINALIZED

        first = ownership.authorize_recovery_cleanup(
            owned,
            observation=observations[0],
            journal=journals[0],
            authority=authority,
            finalized_config=snapshot.current_config,
            finalization=finalizations[0],
        ).unwrap()
        second = ownership.authorize_recovery_cleanup(
            owned,
            observation=observations[0],
            journal=journals[0],
            authority=authority,
            finalized_config=snapshot.current_config,
            finalization=finalizations[0],
        ).unwrap()

        assert first == second
        assert first.location is ownership.TransactionLocation.QUARANTINED
        assert first.journal_head_state == JournalState.FINALIZED.value
        finalized_record = json.loads(first._raw)
        assert finalized_record["journal_head_state"] == JournalState.FINALIZED.value
        assert finalized_record["schema_version"] == "1.1"
        assert finalized_record["writer_version"] == "0.2.0"
        assert finalized_record["minimum_reader_version"] == "0.2.0"
        assert (
            finalized_record["schema_digest"]
            == ownership._TRANSACTION_RECOVERY_CLEANUP_SCHEMA_DIGEST_V1_1
        )
        assert (
            finalized_record["schema_digest"]
            != ownership._TRANSACTION_RECOVERY_CLEANUP_SCHEMA_DIGEST_V1_0
        )
        assert first.finalization_evidence is not None
        assert finalized_record["finalization_evidence"] == (
            json.loads(
                json.dumps(
                    ownership._recovery_cleanup_finalization_projection(
                        first.finalization_evidence
                    )
                )
            )
        )
        assert (
            finalized_record["finalization_evidence_digest"]
            == first.finalization_evidence_digest
        )
        cleanup_observations = ownership.discover_recovery_cleanup_observations(
            owned
        ).unwrap()
        assert len(cleanup_observations) == 1
        assert (
            cleanup_observations[0].authorization.journal_head_state
            == JournalState.FINALIZED.value
        )

        source_manifest.write_bytes(b"resume-boundary-drift\n")
        rejected_resume = ownership.resume_recovery_cleanup(
            owned,
            second,
            authority=authority,
        )
        assert not rejected_resume.is_ok
        assert rejected_resume.error is not None
        assert rejected_resume.error.code == "ownership.cleanup_incomplete"
        retained = ownership.discover_recovery_cleanup_observations(owned).unwrap()
        assert len(retained) == 1
        assert retained[0].phase == "AUTHORIZED"
        assert retained[0].current_reference == second.journal_relative
        assert (tmp_path / "codex-home/plugins" / binding.quarantine_relative).is_dir()
        source_manifest.write_bytes(original_manifest)

        real_remove = ownership.remove_quarantine
        remove_boundary_injected = False

        def mutate_source_then_remove(*args, **kwargs):
            nonlocal remove_boundary_injected
            source_manifest.write_bytes(b"remove-boundary-drift\n")
            remove_boundary_injected = True
            return real_remove(*args, **kwargs)

        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "remove_quarantine",
                mutate_source_then_remove,
            )
            rejected_remove = ownership.resume_recovery_cleanup(
                owned,
                second,
                authority=authority,
            )

        assert remove_boundary_injected
        assert not rejected_remove.is_ok
        assert rejected_remove.error is not None
        assert rejected_remove.error.code == "ownership.cleanup_incomplete"
        retained = ownership.discover_recovery_cleanup_observations(owned).unwrap()
        assert len(retained) == 1
        assert retained[0].phase == "AUTHORIZED"
        assert retained[0].current_reference == second.journal_relative
        assert (tmp_path / "codex-home/plugins" / binding.quarantine_relative).is_dir()
        source_manifest.write_bytes(original_manifest)

        object.__setattr__(
            first,
            "journal_head_state",
            JournalState.ROLLED_BACK.value,
        )
        with pytest.raises(TypeError, match="authorization changed"):
            first._require_valid()
        object.__setattr__(
            first,
            "journal_head_state",
            JournalState.FINALIZED.value,
        )
        first._require_valid()

        with monkeypatch.context() as context:
            context.setattr(
                ownership,
                "_publish_transaction_cleanup_complete",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError("injected after exact terminal unlink")
                ),
            )
            interrupted = ownership.resume_recovery_cleanup(
                owned,
                second,
                authority=authority,
            )

        assert not interrupted.is_ok
        assert interrupted.error is not None
        assert interrupted.error.code == "ownership.cleanup_incomplete"
        finalizing = ownership.discover_recovery_cleanup_observations(owned).unwrap()
        assert len(finalizing) == 1
        assert finalizing[0].phase == "FINALIZING"
        assert finalizing[0].current_reference is None
        assert (
            finalizing[0].authorization.journal_head_state
            == JournalState.FINALIZED.value
        )

        assert (
            ownership.resume_recovery_cleanup(
                owned,
                second,
                authority=authority,
            )
            .unwrap()
            .removed
        )
        assert ownership.discover_pending_transactions(owned).unwrap() == ()
        assert ownership.discover_recovery_cleanup_observations(owned).unwrap() == ()
    finally:
        if ticket is not None:
            ticket.close()
        store.close()
        proof.close()
        owned.close()


def test_quarantined_forward_journal_must_be_finalized_before_cleanup(
    tmp_path: Path,
) -> None:
    from zagrosi_forge.install.contracts import ForgeError
    from zagrosi_forge.install.journal import (
        JournalState,
        JournalTransition,
        load_pending,
    )
    from zagrosi_forge.install.paths import PlatformPathAuthority

    authority = PlatformPathAuthority()
    store, proof, owned, _directory, binding = _store(
        tmp_path,
        authority=authority,
    )
    ticket = None
    try:
        prepared = _prepared(binding)
        descriptor = _config_recovery_descriptor(prepared.transaction_id)
        head = _advance_to_commit_intent(store, prepared, descriptor)
        head = store.append(
            head,
            JournalTransition(
                JournalState.CONFIG_COMMITTED,
                config_result=_config_result(prepared, descriptor),
            ),
        )
        store.append(
            head,
            JournalTransition(
                JournalState.RECEIPT_COMMITTED,
                receipt_result=_receipt_result(prepared),
            ),
        )
        store.close()
        ticket = _quarantine_transaction_root(
            authority,
            owned,
            proof,
            binding,
        )
        ticket.close()

        with pytest.raises(ForgeError) as raised:
            load_pending(owned)

        assert raised.value.code == "journal.corrupt"
        assert (tmp_path / "codex-home/plugins" / binding.quarantine_relative).is_dir()
    finally:
        if ticket is not None:
            ticket.close()
        store.close()
        proof.close()
        owned.close()


def test_rolled_back_cleanup_authorization_keeps_terminal_state_contract(
    tmp_path: Path,
) -> None:
    import zagrosi_forge.install.ownership as ownership
    from zagrosi_forge.install.journal import JournalState, load_pending

    _authority, owned, _binding, _prepared_record, ticket = (
        _completed_quarantined_rollback(tmp_path)
    )
    try:
        ticket.close()
        observations = ownership.discover_pending_transactions(owned).unwrap()
        journals = load_pending(owned)

        authorized = ownership.authorize_recovery_cleanup(
            owned,
            observation=observations[0],
            journal=journals[0],
        ).unwrap()

        assert authorized.journal_head_state == JournalState.ROLLED_BACK.value
        rollback_record = json.loads(authorized._raw)
        assert rollback_record["journal_head_state"] == JournalState.ROLLED_BACK.value
        assert rollback_record["schema_version"] == "1.0"
        assert rollback_record["writer_version"] == "0.2.0"
        assert rollback_record["minimum_reader_version"] == "0.2.0"
        assert (
            rollback_record["schema_digest"]
            == ownership._TRANSACTION_RECOVERY_CLEANUP_SCHEMA_DIGEST_V1_0
        )
        assert ownership.resume_recovery_cleanup(owned, authorized).unwrap().removed
    finally:
        ticket.close()
        owned.close()
