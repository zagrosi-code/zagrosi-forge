"""Forge recovery."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import os

from . import detached_authority as _detached_authority
from . import detached_contract as _detached_contract
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import pinners as _pinners
from . import planning_snapshot as _planning_snapshot
from . import secure_io as _secure_io
from . import transaction_io as _transaction_io
from . import transaction_state as _transaction_state

def recover_section_record_transaction_locked(
    planning_dir: Path,
    implementation_root: Path,
    root_fd: int,
    config: dict[str, Any],
    guard: _planning_snapshot.FrozenPlanningTree,
    progress: dict[str, Any],
    require_lock_authority,
) -> None:
    transaction_fd = _transaction_io.section_record_transaction_dir(root_fd)
    if transaction_fd is None:
        return
    close_transaction_fd = True
    try:
        inventory = _transaction_state.section_record_transaction_inventory(transaction_fd)
        if "transaction.json" in inventory and "rollback.json" in inventory:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Section-record transaction contains both forward and rollback journals.",
            )
        if "rollback.json" in inventory:
            if not inventory.issubset({"rollback.json", "pinner.json", "state.json"}):
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "Published section-record rollback contains unrecognised retained members.",
                )
            if "pinner.json" not in inventory:
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "Published section-record rollback is missing its ownership-proving staged pinner.",
                )
            transaction, transaction_raw = _secure_io.load_canonical_json_at(transaction_fd, "rollback.json")
            state, current_state_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
            _handoff_wire.require_exact_fields(state, _detached_contract.DETACHED_STATE_FIELDS, "Detached implementation state")
            base_state, base_raw, _, candidate_raw = _transaction_state.validate_section_record_transaction(
                transaction,
                state,
            )
            if current_state_raw not in {base_raw, candidate_raw}:
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "Rollback root state is neither the exact transaction base nor exact candidate.",
                )
            if "state.json" in inventory:
                staged_base = _secure_io.read_single_link_regular_at(
                    transaction_fd,
                    "state.json",
                    cap=_detached_contract.DETACHED_JSON_CAP,
                    require_mode=0o600,
                )
                if current_state_raw != candidate_raw or staged_base != base_raw:
                    raise _models.DetachedImplementationError(
                        "section-record-recovery-required",
                        "Rollback state temp is not the exact base staged against the exact candidate.",
                    )
            section = transaction["section"]
            pinner_path = transaction["pinner_path"]
            staged_raw, _ = _transaction_io.read_regular_at_allow_links(
                transaction_fd,
                "pinner.json",
                allowed_link_counts={1, 2},
            )
            if _handoff_wire.sha256_digest(staged_raw) != transaction["pinner_file_sha256"]:
                raise _models.DetachedImplementationError(
                    "section-record-pinner-drift",
                    "Rollback staged pinner does not match its transaction digest.",
                )
            staged_pinner = _secure_io.load_canonical_json_bytes(staged_raw, "Rollback staged section pinner")
            _pinners.verify_section_pinner_bytes(
                root_fd,
                config,
                section,
                transaction["state_record"],
                staged_pinner,
                staged_raw,
                verify_predecessors=False,
            )

            def validate_recovered_rollback_base() -> None:
                require_lock_authority()
                _detached_authority.verify_detached_authorities(
                    planning_dir,
                    implementation_root,
                    root_fd,
                    config,
                    guard,
                )
                _pinners.detached_completed_records(root_fd, config, progress)
                require_lock_authority()

            if not _transaction_io.execute_section_record_rollback(
                root_fd,
                transaction_fd,
                transaction_raw,
                pinner_path,
                staged_raw,
                candidate_raw,
                base_state,
                base_raw,
                validate_recovered_rollback_base,
            ):
                close_transaction_fd = False
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "Section-record rollback closed its state but left idempotent cleanup pending.",
                )
            close_transaction_fd = False
            return
        if "transaction.json" not in inventory:
            if "state.json" in inventory:
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "No-journal section-record state temp is unreachable and was retained for explicit recovery.",
                )
            if not inventory.issubset(
                {"pinner.tmp", "pinner.json", "transaction.write.tmp", "transaction.tmp"}
            ):
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "No-journal section-record transaction contains unrecognised retained members.",
                )
            for name in inventory:
                _transaction_io.require_safe_section_record_file(
                    transaction_fd,
                    name,
                    allowed_link_counts={1, 2} if name == "pinner.json" else {1},
                )
            pending: tuple[str, dict[str, Any], bytes] | None = None
            state, current_state_raw = _secure_io.load_canonical_json_at(
                root_fd,
                "zagrosi_implement_state.json",
            )
            _handoff_wire.require_exact_fields(state, _detached_contract.DETACHED_STATE_FIELDS, "Detached implementation state")
            if "pinner.tmp" in inventory:
                if inventory != {"pinner.tmp"}:
                    raise _models.DetachedImplementationError(
                        "section-record-recovery-required",
                        "A pre-publication pinner temp cannot coexist with another transaction member.",
                    )
                _pinners.detached_completed_records(root_fd, config, progress)
            elif "transaction.write.tmp" in inventory:
                if inventory != {"pinner.json", "transaction.write.tmp"}:
                    raise _models.DetachedImplementationError(
                        "section-record-recovery-required",
                        "A journal write temp requires exactly its published staged pinner.",
                    )
                _, derived_state_record, staged_raw = _transaction_state.verify_no_journal_base_stage(
                    root_fd,
                    transaction_fd,
                    config,
                    state,
                    progress,
                )
                write_temp_raw = _secure_io.read_single_link_regular_at(
                    transaction_fd,
                    "transaction.write.tmp",
                    cap=_detached_contract.DETACHED_JSON_CAP,
                    require_mode=0o600,
                )
                try:
                    write_transaction = _secure_io.load_canonical_json_bytes(
                        write_temp_raw,
                        "Pre-publication journal write temp",
                    )
                except _models.DetachedImplementationError:
                    write_transaction = None
                if write_transaction is not None:
                    _handoff_wire.require_exact_fields(
                        write_transaction,
                        _detached_contract.SECTION_RECORD_TRANSACTION_FIELDS,
                        "Section-record transaction",
                    )
                    _, write_base_raw, _, write_candidate_raw = _transaction_state.validate_section_record_transaction(
                        write_transaction,
                        state,
                    )
                    if (
                        current_state_raw != write_base_raw
                        or current_state_raw == write_candidate_raw
                        or write_transaction["state_record"] != derived_state_record
                        or write_transaction["pinner_file_sha256"] != _handoff_wire.sha256_digest(staged_raw)
                    ):
                        raise _models.DetachedImplementationError(
                            "section-record-recovery-required",
                            "Canonical journal write temp does not join its exact staged pinner and distinct base.",
                        )
                _pinners.detached_completed_records(root_fd, config, progress)
            elif "transaction.tmp" in inventory:
                if inventory != {"pinner.json", "transaction.tmp"}:
                    raise _models.DetachedImplementationError(
                        "section-record-recovery-required",
                        "A pre-publication transaction temp requires exactly its published staged pinner.",
                    )
                transaction, _ = _secure_io.load_canonical_json_at(transaction_fd, "transaction.tmp")
                _, base_raw, _, candidate_raw = _transaction_state.validate_section_record_transaction(
                    transaction,
                    state,
                )
                if current_state_raw != base_raw or current_state_raw == candidate_raw:
                    raise _models.DetachedImplementationError(
                        "section-record-recovery-required",
                        "A pre-publication transaction temp requires the exact distinct transaction base state.",
                    )
                staged_pinner, derived_state_record, staged_raw = _transaction_state.verify_no_journal_base_stage(
                    root_fd,
                    transaction_fd,
                    config,
                    state,
                    progress,
                )
                if _handoff_wire.sha256_digest(staged_raw) != transaction["pinner_file_sha256"]:
                    raise _models.DetachedImplementationError(
                        "section-record-pinner-drift",
                        "Pre-publication staged pinner does not match its transaction temp digest.",
                    )
                section = transaction["section"]
                _pinners.verify_section_pinner_bytes(
                    root_fd,
                    config,
                    section,
                    transaction["state_record"],
                    staged_pinner,
                    staged_raw,
                )
                if transaction["state_record"] != derived_state_record:
                    raise _models.DetachedImplementationError(
                        "invalid-section-record-transaction",
                        "Pre-publication transaction temp does not project its exact staged pinner record.",
                    )
                _pinners.detached_completed_records(root_fd, config, progress)
            elif "pinner.json" in inventory:
                staged_raw, staged_stat = _transaction_io.read_regular_at_allow_links(
                    transaction_fd,
                    "pinner.json",
                    allowed_link_counts={1, 2},
                )
                staged_pinner = _secure_io.load_canonical_json_bytes(
                    staged_raw,
                    "Published no-journal staged section pinner",
                )
                state_record = _transaction_state.pinner_state_record(staged_pinner, staged_raw)
                section = staged_pinner["section"]
                _pinners.verify_section_pinner_bytes(
                    root_fd,
                    config,
                    section,
                    state_record,
                    staged_pinner,
                    staged_raw,
                )
                pinner_path = state_record["pinner_path"]
                pinner_parts = _secure_io._relative_parts(pinner_path)
                pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
                try:
                    final_present = _transaction_io.section_record_entry_stat(pinners_fd, pinner_parts[1]) is not None
                finally:
                    os.close(pinners_fd)
                state_has_candidate = state.get("completed_sections", {}).get(section) == state_record
                if state_has_candidate:
                    if not final_present:
                        raise _models.DetachedImplementationError(
                            "section-record-recovery-required",
                            "No-journal candidate state is missing its exact final pinner.",
                        )
                    _transaction_io.section_record_pinner_relation(
                        root_fd,
                        transaction_fd,
                        pinner_path,
                        staged_raw,
                    )
                    pending = (section, staged_pinner, staged_raw)
                else:
                    verified_pinner, verified_record, verified_raw = _transaction_state.verify_no_journal_base_stage(
                        root_fd,
                        transaction_fd,
                        config,
                        state,
                        progress,
                    )
                    if (
                        verified_pinner != staged_pinner
                        or verified_record != state_record
                        or verified_raw != staged_raw
                        or staged_stat.st_nlink != 1
                    ):
                        raise _models.DetachedImplementationError(
                            "section-record-recovery-required",
                            "No-journal base-stage provenance changed during validation.",
                        )
                _pinners.detached_completed_records(
                    root_fd,
                    config,
                    progress,
                    pending_pinner=pending,
                )
            else:
                _pinners.detached_completed_records(root_fd, config, progress)
            require_lock_authority()
            _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
            if not _transaction_io.cleanup_section_record_transaction_after_commit(root_fd, transaction_fd):
                close_transaction_fd = False
                raise _models.DetachedImplementationError(
                    "section-record-committed-cleanup-pending",
                    "Committed or pre-journal section-record residue could not be cleaned safely.",
                )
            close_transaction_fd = False
            return
        if not inventory.issubset({"transaction.json", "pinner.json", "state.json"}):
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Published section-record transaction contains unrecognised retained members.",
            )
        transaction, transaction_raw = _secure_io.load_canonical_json_at(transaction_fd, "transaction.json")
        state, current_state_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
        _handoff_wire.require_exact_fields(state, _detached_contract.DETACHED_STATE_FIELDS, "Detached implementation state")
        base_state, base_raw, candidate_state, candidate_raw = _transaction_state.validate_section_record_transaction(
            transaction,
            state,
        )
        if current_state_raw not in {base_raw, candidate_raw}:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Detached state is neither the exact transaction base nor exact candidate.",
            )
        if "state.json" in inventory:
            staged_candidate = _secure_io.read_single_link_regular_at(
                transaction_fd,
                "state.json",
                cap=_detached_contract.DETACHED_JSON_CAP,
                require_mode=0o600,
            )
            if current_state_raw != base_raw or staged_candidate != candidate_raw:
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "Forward state temp is not the exact candidate staged against the exact base.",
                )
        section = transaction["section"]
        pinner_path = transaction["pinner_path"]
        pinner_file_sha256 = transaction["pinner_file_sha256"]
        staged_present = "pinner.json" in inventory
        pinner_parts = _secure_io._relative_parts(pinner_path)
        if len(pinner_parts) != 2 or pinner_parts[0] != "pinners":
            raise _models.DetachedImplementationError(
                "invalid-section-record-transaction",
                "Published section-record pinner path is not an immediate pinners child.",
            )
        pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
        try:
            final_present = _transaction_io.section_record_entry_stat(pinners_fd, pinner_parts[1]) is not None
        finally:
            os.close(pinners_fd)
        if not staged_present:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Published section-record transaction is missing its ownership-proving staged pinner.",
            )
        staged_raw, _ = _transaction_io.read_regular_at_allow_links(
            transaction_fd,
            "pinner.json",
            allowed_link_counts={1, 2},
        )
        if _handoff_wire.sha256_digest(staged_raw) != pinner_file_sha256:
            raise _models.DetachedImplementationError(
                "section-record-pinner-drift",
                "Published staged pinner does not match its transaction digest.",
            )
        staged_pinner = _secure_io.load_canonical_json_bytes(staged_raw, "Published staged section pinner")
        _pinners.verify_section_pinner_bytes(
            root_fd,
            config,
            section,
            transaction["state_record"],
            staged_pinner,
            staged_raw,
            verify_predecessors=False,
        )
        def rollback_candidate_after_recovery_failure(cause: Exception) -> None:
            nonlocal close_transaction_fd
            _, observed_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
            if observed_raw not in {base_raw, candidate_raw}:
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "Section-record recovery failed after state changed outside its exact base/candidate projections.",
                ) from cause
            try:
                def validate_failed_candidate_rollback_base() -> None:
                    require_lock_authority()
                    _detached_authority.verify_detached_authorities(
                        planning_dir,
                        implementation_root,
                        root_fd,
                        config,
                        guard,
                    )
                    _pinners.detached_completed_records(root_fd, config, progress)
                    require_lock_authority()

                cleanup_complete = _transaction_io.execute_section_record_rollback(
                    root_fd,
                    transaction_fd,
                    transaction_raw,
                    pinner_path,
                    staged_raw,
                    candidate_raw,
                    base_state,
                    base_raw,
                    validate_failed_candidate_rollback_base,
                )
                close_transaction_fd = False
                if not cleanup_complete:
                    raise _models.DetachedImplementationError(
                        "section-record-recovery-required",
                        "Rollback closed its state but left idempotent cleanup pending.",
                    )
            except Exception as rollback_exc:
                raise _models.DetachedImplementationError(
                    "section-record-recovery-required",
                    "Candidate recovery failed and exact state/pinner rollback could not be proven.",
                ) from rollback_exc
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Candidate recovery validation failed; state was rolled back and transaction artefacts were retained.",
            ) from cause

        if not final_present:
            if current_state_raw == candidate_raw:
                rollback_candidate_after_recovery_failure(
                    _models.DetachedImplementationError(
                        "section-record-recovery-required",
                        "Candidate state retained a staged pinner without its final pinner.",
                    )
                )
            _transaction_io.install_staged_section_pinner(root_fd, transaction_fd, pinner_path, staged_raw)
        _transaction_io.section_record_pinner_relation(root_fd, transaction_fd, pinner_path, staged_raw)

        if current_state_raw == base_raw:
            try:
                _transaction_state.verify_section_record_artifact_closure(
                    planning_dir,
                    implementation_root,
                    root_fd,
                    config,
                    guard,
                    progress,
                    section,
                    staged_pinner,
                    staged_raw,
                    base_raw,
                    require_lock_authority,
                )
                _transaction_io.replace_state_from_transaction(root_fd, transaction_fd, base_raw, candidate_state)
                current_state_raw = candidate_raw
            except Exception as exc:
                rollback_candidate_after_recovery_failure(exc)
        try:
            _transaction_state.verify_section_record_artifact_closure(
                planning_dir,
                implementation_root,
                root_fd,
                config,
                guard,
                progress,
                section,
                staged_pinner,
                staged_raw,
                candidate_raw,
                require_lock_authority,
            )
            require_lock_authority()
            _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
            _transaction_io.verify_section_record_commit_closure(
                root_fd,
                transaction_fd,
                transaction_raw,
                pinner_path,
                staged_raw,
                candidate_raw,
            )
            require_lock_authority()
        except Exception as exc:
            rollback_candidate_after_recovery_failure(exc)
        if not _transaction_io.commit_section_record_transaction(root_fd, transaction_fd):
            close_transaction_fd = False
            raise _models.DetachedImplementationError(
                "section-record-committed-cleanup-pending",
                "Section record committed, but idempotent transaction cleanup remains pending.",
            )
        close_transaction_fd = False
    finally:
        if close_transaction_fd:
            os.close(transaction_fd)
