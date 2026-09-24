"""Forge detached record."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple
import argparse
import json
import os

from . import detached_authority as _detached_authority
from . import detached_context as _detached_context
from . import detached_contract as _detached_contract
from . import detached_state as _detached_state
from . import gates as _gates
from . import handoff_host as _handoff_host
from . import handoff_wire as _handoff_wire
from . import markdown as _markdown
from . import models as _models
from . import output as _output
from . import pinners as _pinners
from . import sections as _sections
from . import secure_io as _secure_io
from . import storage as _storage
from . import transaction_io as _transaction_io
from . import transaction_state as _transaction_state
from . import validation as _validation

def _record_predecessors(planning_dir, root_fd, config, args):
    """Admit a known section only after every predecessor has a closed receipt."""
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"}:
        raise _models.DetachedImplementationError(
            "invalid-sections-index",
            "Cannot record detached implementation against an invalid sections index.",
            section_progress=progress,
        )
    artifact_payload = _validation.plan_artifacts_payload(planning_dir, argparse.Namespace(profile=args.profile, strict=True, allow_compact=False))
    if not artifact_payload["success"]:
        raise _models.DetachedImplementationError(
            "incomplete-plan-artifacts",
            "Forge planning process is incomplete; finish zagrosi-plan before recording implementation.",
            findings=artifact_payload.get("findings", []),
        )
    section = args.section
    known = set(progress["sections"])
    if section not in known:
        raise _models.DetachedImplementationError(
            "unknown-section",
            f"Section is absent from SECTION_MANIFEST: {section}",
            section=section,
        )
    dependencies = _sections.dependency_graph(planning_dir, progress)
    unknown_predecessors = sorted(dependency for dependency in dependencies.get(section, []) if dependency not in known)
    if unknown_predecessors:
        raise _models.DetachedImplementationError(
            "unknown-predecessors",
            f"Section names predecessors absent from SECTION_MANIFEST: {section}",
            section=section,
            unknown_predecessors=unknown_predecessors,
        )
    completed_records = _pinners.detached_completed_records(root_fd, config, progress)
    initial_state, _ = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if initial_state.get("completed_sections") != completed_records:
        raise _models.DetachedImplementationError(
            "detached-state-drift",
            "Detached implementation state changed during initial predecessor validation.",
            section=section,
        )
    completed_dependants = _pinners.completed_transitive_dependants(
        section,
        dependencies,
        set(completed_records),
    )
    if section in completed_records and completed_dependants:
        raise _models.DetachedImplementationError(
            "completed-dependent-pinner-conflict",
            f"Section cannot be re-recorded while completed transitive dependants pin its current receipt: {section}",
            section=section,
            completed_dependants=completed_dependants,
        )
    incomplete_predecessors = [dependency for dependency in dependencies.get(section, []) if dependency not in completed_records]
    if incomplete_predecessors:
        raise _models.DetachedImplementationError(
            "incomplete-predecessors",
            f"Section cannot be recorded before every predecessor pinner closes: {section}",
            section=section,
            incomplete_predecessors=incomplete_predecessors,
        )

    return progress, dependencies, completed_records


def _record_pinner(planning_dir, context, args, progress, dependencies, completed_records):
    """Revalidate evidence at each existing checkpoint before constructing the receipt."""
    implementation_root, root_fd, config, guard, _ = context
    section = args.section
    evidence_values = _detached_state.detached_section_evidence_values(section, args.evidence_rows)
    review_rows = _detached_state.detached_review_rows(root_fd, implementation_root, section, args.review_artifacts)
    if section in _detached_contract.HANDOFF_CONTRACT_BY_SECTION:
        _handoff_host.verify_stored_privileged_handoff(
            planning_dir,
            implementation_root,
            root_fd,
            config,
            guard,
            section,
        )
    evidence_rows = _detached_state.detached_evidence_rows(root_fd, implementation_root, evidence_values)
    _detached_state.require_privileged_section_evidence(section, evidence_rows)
    verification = _markdown.normalize_repeated(args.verification)
    if evidence_rows and not verification:
        raise _models.DetachedImplementationError(
            "missing-evidence-verification",
            "Detached evidence rows require at least one section verification command that semantically validates them.",
            section=section,
        )

    final_review_rows = _detached_state.detached_review_rows(root_fd, implementation_root, section, args.review_artifacts)
    if final_review_rows != review_rows:
        raise _models.DetachedImplementationError(
            "detached-review-drift",
            f"Detached review artifacts changed before section pinner creation: {section}",
            section=section,
        )
    if section in _detached_contract.HANDOFF_CONTRACT_BY_SECTION:
        _handoff_host.verify_stored_privileged_handoff(
            planning_dir,
            implementation_root,
            root_fd,
            config,
            guard,
            section,
        )
    final_evidence_rows = _detached_state.detached_evidence_rows(root_fd, implementation_root, evidence_values)
    _detached_state.require_privileged_section_evidence(section, final_evidence_rows)
    if final_evidence_rows != evidence_rows:
        raise _models.DetachedImplementationError(
            "detached-evidence-drift",
            f"Detached evidence changed before section pinner creation: {section}",
            section=section,
            expected_evidence_rows=evidence_rows,
            actual_evidence_rows=final_evidence_rows,
        )
    _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
    final_completed_records = _pinners.detached_completed_records(root_fd, config, progress)
    if final_completed_records != completed_records:
        raise _models.DetachedImplementationError(
            "detached-state-drift",
            f"Detached predecessor state changed before section pinner creation: {section}",
            section=section,
        )
    _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)

    predecessor_pinners: list[dict[str, Any]] = []
    for predecessor in dependencies.get(section, []):
        predecessor_record = final_completed_records[predecessor]
        _, file_sha256 = _pinners.verify_section_pinner(root_fd, config, predecessor, predecessor_record)
        predecessor_pinners.append(
            {
                "section": predecessor,
                "pinner_path": predecessor_record["pinner_path"],
                "pinner_file_sha256": file_sha256,
            }
        )

    last_completed_records = _pinners.detached_completed_records(root_fd, config, progress)
    if last_completed_records != final_completed_records:
        raise _models.DetachedImplementationError(
            "detached-state-drift",
            f"Detached predecessor state changed during final section validation: {section}",
            section=section,
        )
    last_review_rows = _detached_state.detached_review_rows(root_fd, implementation_root, section, args.review_artifacts)
    if last_review_rows != final_review_rows:
        raise _models.DetachedImplementationError(
            "detached-review-drift",
            f"Detached review artifacts changed during final section validation: {section}",
            section=section,
        )
    last_evidence_rows = _detached_state.detached_evidence_rows(root_fd, implementation_root, evidence_values)
    _detached_state.require_privileged_section_evidence(section, last_evidence_rows)
    if last_evidence_rows != final_evidence_rows:
        raise _models.DetachedImplementationError(
            "detached-evidence-drift",
            f"Detached evidence changed during final section validation: {section}",
            section=section,
            expected_evidence_rows=final_evidence_rows,
            actual_evidence_rows=last_evidence_rows,
        )
    _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
    privileged_receipt_raw: bytes | None = None
    if section in _detached_contract.HANDOFF_CONTRACT_BY_SECTION:
        _, privileged_receipt_raw = _handoff_host.verify_stored_privileged_handoff(
            planning_dir,
            implementation_root,
            root_fd,
            config,
            guard,
            section,
        )
    _detached_state.require_verified_privileged_evidence_bytes(section, last_evidence_rows, privileged_receipt_raw)

    completed_at = _storage.now_iso()
    files_changed = _markdown.normalize_repeated(args.files_changed)
    test_files = _markdown.normalize_repeated(args.test_files)
    commit_status = args.commit_status or ("recorded" if args.commit else "not_recorded")
    pinner = {
        "schema": _detached_contract.SECTION_PINNER_SCHEMA,
        "section": section,
        "planning_tree_sha256": config["planning_tree_sha256"],
        "admission_pinner_sha256": config["admission_pinner_sha256"],
        "admission_state_sha256": config["admission_state_sha256"],
        "detached_implementation_root_identity_digest": config[
            "detached_implementation_root_identity_digest"
        ],
        "target_root_identity_digest": config["target_root_identity_digest"],
        "implement_tool_sha256": config["implement_tool_sha256"],
        "implement_skill_sha256": config["implement_skill_sha256"],
        "implement_test_sha256": config["implement_test_sha256"],
        "completed_at": completed_at,
        "commit": args.commit,
        "commit_status": commit_status,
        "notes": args.notes,
        "files_changed": files_changed,
        "test_files": test_files,
        "review_artifacts": final_review_rows,
        "evidence_rows": final_evidence_rows,
        "verification": verification,
        "predecessor_pinners": predecessor_pinners,
    }
    _handoff_wire.require_exact_fields(pinner, _detached_contract.SECTION_PINNER_FIELDS, f"Section pinner for {section}")
    pinner_raw = _handoff_wire.canonical_json_bytes(pinner)
    state_record = _transaction_state.pinner_state_record(pinner, pinner_raw)

    return pinner, pinner_raw, state_record


def _record_payload(planning_dir, context, args, progress, dependencies, candidate_state, state_record):
    """Describe the candidate result; publication sets its final transaction status."""
    implementation_root, _, config, guard, _ = context
    section = args.section
    pinner_path = state_record["pinner_path"]
    pinner_file_sha256 = state_record["pinner_file_sha256"]
    completed_after = set(candidate_state["completed_sections"])
    ready_after = _sections.ready_sections(progress, dependencies, completed_after)
    remaining_after = [candidate for candidate in progress["sections"] if candidate not in completed_after]
    payload = {
        "success": True,
        "mode": "detached-frozen",
        "planning_dir": str(planning_dir),
        "implementation_root": str(implementation_root),
        "planning_tree_sha256": guard.digest,
        "admission_pinner_sha256": config["admission_pinner_sha256"],
        "admission_state_sha256": config["admission_state_sha256"],
        "detached_implementation_root_identity_digest": config[
            "detached_implementation_root_identity_digest"
        ],
        "state_path": str(implementation_root / "zagrosi_implement_state.json"),
        "section": section,
        "record": state_record,
        "pinner_path": str(implementation_root / pinner_path),
        "pinner_file_sha256": pinner_file_sha256,
        "traceability_matrix": None,
        "completed_sections": sorted(completed_after),
        "next_section": ready_after[0] if ready_after else None,
        "ready_sections": ready_after,
        "remaining_sections": remaining_after,
        "transaction_status": "pending",
        "transaction_cleanup_pending": False,
    }
    if _gates.effective_flight_mode(args) != "off":
        payload["postflight"] = _gates.flight_payload(
            phase="implement",
            stage="postflight",
            mode=_gates.effective_flight_mode(args),
            gates=[
                _gates.direct_gate(
                    "detached-section-pinner",
                    True,
                    {"path": payload["pinner_path"], "sha256": pinner_file_sha256},
                ),
                _gates.direct_gate("frozen-planning-tree", True, {"sha256": guard.digest}),
            ],
            extras={"planning_dir": str(planning_dir), "implementation_root": str(implementation_root)},
        )
    return payload


class _RecordTransaction(NamedTuple):
    journal: dict[str, Any]
    journal_raw: bytes
    pinner_raw: bytes
    base_state: dict[str, Any]
    base_raw: bytes
    candidate_state: dict[str, Any]
    candidate_raw: bytes


def _rollback_failed_record(context, record, transaction_fd, validate_base) -> bool:
    """Consume the transaction descriptor; retain unprovable state for recovery."""
    root_fd = context.root_fd
    try:
        _, observed = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
        if observed not in {record.base_raw, record.candidate_raw}:
            return False
        context.require_lock_authority()
        journal_present = _transaction_io.section_record_entry_stat(transaction_fd, "transaction.json") is not None
        rollback_present = _transaction_io.section_record_entry_stat(transaction_fd, "rollback.json") is not None
        if not (journal_present or rollback_present):
            cleanup_safe = _transaction_io.abort_section_record_transaction(root_fd, transaction_fd)
        else:
            if _transaction_io.section_record_entry_stat(transaction_fd, "state.json") is not None:
                staged_state = _secure_io.read_single_link_regular_at(
                    transaction_fd, "state.json", cap=_detached_contract.DETACHED_JSON_CAP, require_mode=0o600,
                )
                expected = record.candidate_raw if journal_present and observed == record.base_raw else record.base_raw
                if staged_state != expected:
                    raise _models.DetachedImplementationError(
                        "section-record-recovery-required",
                        "Section-record state temp was not reachable from the failed transaction state.",
                    )
            cleanup_safe = _transaction_io.execute_section_record_rollback(
                root_fd, transaction_fd, record.journal_raw, record.journal["pinner_path"], record.pinner_raw,
                record.candidate_raw, record.base_state, record.base_raw, validate_base,
            )
        transaction_fd = None
        return cleanup_safe
    except Exception:
        return False
    finally:
        if transaction_fd is not None:
            os.close(transaction_fd)


def _publish_record(context, record, verify_artifacts, validate_authority, validate_base) -> bool:
    """Publish the pinner and state with closure checks around every durable step."""
    root_fd = context.root_fd
    pinner_path = record.journal["pinner_path"]
    transaction_fd = None
    verify_artifacts(record.base_raw)
    try:
        transaction_fd = _transaction_io.section_record_transaction_dir(root_fd, create=True)
        assert transaction_fd is not None
        if _transaction_state.section_record_transaction_inventory(transaction_fd):
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Section-record transaction directory was not empty after locked recovery.",
            )
        _transaction_io.publish_section_record_staged_pinner(transaction_fd, record.pinner_raw)
        if _transaction_io.publish_section_record_transaction(
            root_fd, transaction_fd, record.journal, record.base_raw,
        ) != record.journal_raw:
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Published section-record transaction bytes changed before use.",
            )
        _transaction_io.install_staged_section_pinner(root_fd, transaction_fd, pinner_path, record.pinner_raw)
        _transaction_io.section_record_pinner_relation(root_fd, transaction_fd, pinner_path, record.pinner_raw)
        verify_artifacts(record.base_raw)
        _transaction_io.replace_state_from_transaction(root_fd, transaction_fd, record.base_raw, record.candidate_state)
        verify_artifacts(record.candidate_raw)
        context.require_lock_authority()
        validate_authority()
        _transaction_io.verify_section_record_commit_closure(
            root_fd, transaction_fd, record.journal_raw, pinner_path, record.pinner_raw, record.candidate_raw,
        )
        context.require_lock_authority()
        return not _transaction_io.commit_section_record_transaction(root_fd, transaction_fd)
    except Exception as record_exc:
        if transaction_fd is not None and not _rollback_failed_record(context, record, transaction_fd, validate_base):
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Section recording failed and exact rollback/transaction cleanup could not be proven; artefacts were retained.",
            ) from record_exc
        raise


def detached_implement_record_section(args: argparse.Namespace) -> int:
    sections_dir = _storage.absolute_path_no_follow(args.sections_dir)
    planning_dir = sections_dir.parent
    implementation_root: Path | None = None
    try:
        with _detached_context.open_detached_context(
            planning_dir,
            args.implementation_root,
            sections_dir=sections_dir,
        ) as context:
            implementation_root, root_fd, config, guard, require_lock_authority = context
            section = args.section
            progress, dependencies, completed_records = _record_predecessors(planning_dir, root_fd, config, args)
            pinner, pinner_raw, state_record = _record_pinner(
                planning_dir, context, args, progress, dependencies, completed_records,
            )
            pinner_path = state_record["pinner_path"]
            pinner_file_sha256 = state_record["pinner_file_sha256"]

            require_lock_authority()
            _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
            locked_completed_records = _pinners.detached_completed_records(root_fd, config, progress)
            if locked_completed_records != completed_records:
                raise _models.DetachedImplementationError(
                    "detached-state-drift",
                    f"Detached predecessor/current state changed before transaction preparation: {section}",
                    section=section,
                )
            base_state = _detached_state.load_detached_state(root_fd, config)
            _, base_state_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
            if base_state.get("completed_sections") != locked_completed_records:
                raise _models.DetachedImplementationError(
                    "detached-state-drift",
                    "Detached state projection changed before section transaction preparation.",
                    section=section,
                )
            candidate_state = json.loads(base_state_raw.decode("utf-8"))
            prior_state_record = candidate_state["completed_sections"].get(section)
            candidate_state["completed_sections"][section] = state_record
            candidate_state_raw = _handoff_wire.canonical_json_bytes(candidate_state)
            if candidate_state_raw == base_state_raw:
                raise _models.DetachedImplementationError(
                    "section-record-state-conflict",
                    "Section record is an exact no-op against the current canonical state.",
                    section=section,
                )
            payload = _record_payload(planning_dir, context, args, progress, dependencies, candidate_state, state_record)
            transaction = {
                "schema": _detached_contract.SECTION_RECORD_TRANSACTION_SCHEMA,
                "section": section,
                "base_state_sha256": _handoff_wire.sha256_digest(base_state_raw),
                "candidate_state_sha256": _handoff_wire.sha256_digest(candidate_state_raw),
                "prior_state_record": prior_state_record,
                "state_record": state_record,
                "pinner_path": pinner_path,
                "pinner_file_sha256": pinner_file_sha256,
            }
            _handoff_wire.require_exact_fields(transaction, _detached_contract.SECTION_RECORD_TRANSACTION_FIELDS, "Section-record transaction")
            transaction_raw = _handoff_wire.canonical_json_bytes(transaction)
            _pinners.verify_section_pinner_bytes(root_fd, config, section, state_record, pinner, pinner_raw)
            record = _RecordTransaction(
                transaction, transaction_raw, pinner_raw, base_state, base_state_raw, candidate_state, candidate_state_raw,
            )

            def verify_artifacts(expected_state_raw):
                _transaction_state.verify_section_record_artifact_closure(
                    planning_dir, implementation_root, root_fd, config, guard, progress,
                    section, pinner, pinner_raw, expected_state_raw, require_lock_authority,
                )

            def validate_authority():
                _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)

            def validate_base():
                require_lock_authority()
                validate_authority()
                _pinners.detached_completed_records(root_fd, config, progress)
                require_lock_authority()

            cleanup_pending = _publish_record(context, record, verify_artifacts, validate_authority, validate_base)
            payload["transaction_cleanup_pending"] = cleanup_pending
            payload["transaction_status"] = (
                "committed-cleanup-pending" if cleanup_pending else "committed-clean"
            )
            return _output.print_json(payload)
    except (_models.DetachedImplementationError, OSError) as exc:
        error_payload = _models.detached_error_payload if isinstance(exc, _models.DetachedImplementationError) else _models.detached_io_error_payload
        return _output.print_json(
            error_payload(
                exc,
                mode="detached-frozen",
                planning_dir=str(planning_dir),
                implementation_root=str(implementation_root or _storage.absolute_path_no_follow(args.implementation_root)),
            ),
            1,
        )
