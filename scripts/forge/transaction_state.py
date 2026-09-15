"""Forge transaction state."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os
import re

from . import detached_authority as _detached_authority
from . import detached_contract as _detached_contract
from . import detached_state as _detached_state
from . import handoff_host as _handoff_host
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import pinners as _pinners
from . import planning_snapshot as _planning_snapshot
from . import policy as _policy
from . import sections as _sections
from . import secure_io as _secure_io
from . import storage as _storage
from . import transaction_io as _transaction_io

def require_sha256_field(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            f"Section-record transaction {label} is not an exact lowercase sha256 digest.",
        )
    return value


def section_record_transaction_inventory(transaction_fd: int) -> set[str]:
    first = set(os.listdir(transaction_fd))
    if set(os.listdir(transaction_fd)) != first:
        raise _models.DetachedImplementationError(
            "unsafe-section-record-transaction",
            "Section-record transaction inventory changed while it was observed.",
        )
    return first


def section_record_transaction_states(
    current_state: dict[str, Any],
    transaction: dict[str, Any],
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    section = transaction.get("section")
    prior_record = transaction.get("prior_state_record")
    state_record = transaction.get("state_record")
    if not isinstance(section, str) or not section:
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record transaction section is invalid.",
        )
    if prior_record is not None:
        if not isinstance(prior_record, dict):
            raise _models.DetachedImplementationError(
                "invalid-section-record-transaction",
                "Section-record transaction prior state record is invalid.",
            )
        _handoff_wire.require_exact_fields(prior_record, _policy.PINNER_STATE_RECORD_FIELDS, "Transaction prior state record")
    if not isinstance(state_record, dict):
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record transaction candidate state record is invalid.",
        )
    _handoff_wire.require_exact_fields(state_record, _policy.PINNER_STATE_RECORD_FIELDS, "Transaction candidate state record")
    base_state = json.loads(_handoff_wire.canonical_json_bytes(current_state).decode("utf-8"))
    candidate_state = json.loads(_handoff_wire.canonical_json_bytes(current_state).decode("utf-8"))
    base_completed = base_state["completed_sections"]
    candidate_completed = candidate_state["completed_sections"]
    if prior_record is None:
        base_completed.pop(section, None)
    else:
        base_completed[section] = prior_record
    candidate_completed[section] = state_record
    base_raw = _handoff_wire.canonical_json_bytes(base_state)
    candidate_raw = _handoff_wire.canonical_json_bytes(candidate_state)
    if _handoff_wire.sha256_digest(base_raw) != require_sha256_field(transaction.get("base_state_sha256"), "base_state_sha256"):
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record transaction base-state projection does not match its digest.",
        )
    if _handoff_wire.sha256_digest(candidate_raw) != require_sha256_field(
        transaction.get("candidate_state_sha256"),
        "candidate_state_sha256",
    ):
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record transaction candidate-state projection does not match its digest.",
        )
    return base_state, base_raw, candidate_state, candidate_raw


def validate_section_record_transaction(
    transaction: dict[str, Any],
    current_state: dict[str, Any],
) -> tuple[dict[str, Any], bytes, dict[str, Any], bytes]:
    _handoff_wire.require_exact_fields(transaction, _detached_contract.SECTION_RECORD_TRANSACTION_FIELDS, "Section-record transaction")
    if transaction.get("schema") != _detached_contract.SECTION_RECORD_TRANSACTION_SCHEMA:
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record transaction schema is invalid.",
        )
    pinner_path = transaction.get("pinner_path")
    pinner_file_sha256 = require_sha256_field(transaction.get("pinner_file_sha256"), "pinner_file_sha256")
    state_record = transaction.get("state_record")
    if (
        not isinstance(pinner_path, str)
        or not isinstance(state_record, dict)
        or state_record.get("pinner_path") != pinner_path
        or state_record.get("pinner_file_sha256") != pinner_file_sha256
    ):
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record transaction pinner pointer is not the exact candidate state pointer.",
        )
    return section_record_transaction_states(current_state, transaction)


def verify_section_record_artifact_closure(
    planning_dir: Path,
    implementation_root: Path,
    root_fd: int,
    config: dict[str, Any],
    guard: _planning_snapshot.FrozenPlanningTree,
    progress: dict[str, Any],
    section: str,
    pinner: dict[str, Any],
    pinner_raw: bytes,
    expected_state_raw: bytes,
    require_lock_authority,
) -> None:
    require_lock_authority()
    _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
    _, observed_state_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    if observed_state_raw != expected_state_raw:
        raise _models.DetachedImplementationError(
            "section-record-state-conflict",
            "Section-record state changed during transaction closure validation.",
        )
    state_record = pinner_state_record(pinner, pinner_raw)
    state, _ = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    pending = None
    if state.get("completed_sections", {}).get(section) == state_record:
        pending = (section, pinner, pinner_raw)
    _pinners.detached_completed_records(root_fd, config, progress, pending_pinner=pending)
    review_rows = pinner.get("review_artifacts")
    if not isinstance(review_rows, list) or any(
        not isinstance(row, dict) or set(row) != {"path", "sha256", "size"} for row in review_rows
    ):
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record transaction review projection is invalid.",
        )
    observed_reviews = _detached_state.detached_review_rows(
        root_fd,
        implementation_root,
        section,
        [row["path"] for row in review_rows],
    )
    if observed_reviews != review_rows:
        raise _models.DetachedImplementationError(
            "detached-review-drift",
            f"Detached review artifacts changed during transaction recovery: {section}",
            section=section,
        )
    evidence_rows = pinner.get("evidence_rows")
    if not isinstance(evidence_rows, list) or any(
        not isinstance(row, dict) or set(row) != {"name", "path", "sha256", "size"} for row in evidence_rows
    ):
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Section-record transaction evidence projection is invalid.",
        )
    observed_evidence = _detached_state.detached_evidence_rows(
        root_fd,
        implementation_root,
        [f"{row['name']}={row['path']}" for row in evidence_rows],
    )
    if observed_evidence != evidence_rows:
        raise _models.DetachedImplementationError(
            "detached-evidence-drift",
            f"Detached evidence changed during transaction recovery: {section}",
            section=section,
        )
    privileged_raw: bytes | None = None
    if section in _detached_contract.HANDOFF_CONTRACT_BY_SECTION:
        _, privileged_raw = _handoff_host.verify_stored_privileged_handoff(
            planning_dir,
            implementation_root,
            root_fd,
            config,
            guard,
            section,
        )
    _detached_state.require_verified_privileged_evidence_bytes(section, observed_evidence, privileged_raw)
    _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
    require_lock_authority()


def pinner_state_record(pinner: dict[str, Any], pinner_raw: bytes) -> dict[str, Any]:
    pinner_file_sha256 = _handoff_wire.sha256_digest(pinner_raw)
    section = pinner.get("section")
    if not isinstance(section, str):
        raise _models.DetachedImplementationError(
            "invalid-section-record-transaction",
            "Staged pinner section is invalid.",
        )
    record = {
        field: pinner.get(field)
        for field in _policy.PINNER_STATE_RECORD_FIELDS
        if field not in {"pinner_path", "pinner_file_sha256"}
    }
    record["pinner_path"] = f"pinners/{section}-{pinner_file_sha256.removeprefix('sha256:')}.json"
    record["pinner_file_sha256"] = pinner_file_sha256
    _handoff_wire.require_exact_fields(record, _policy.PINNER_STATE_RECORD_FIELDS, "Staged pinner state record")
    return record


def verify_no_journal_base_stage(
    root_fd: int,
    transaction_fd: int,
    config: dict[str, Any],
    state: dict[str, Any],
    progress: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    staged_raw, staged_stat = _transaction_io.read_regular_at_allow_links(
        transaction_fd,
        "pinner.json",
        allowed_link_counts={1},
    )
    staged_pinner = _secure_io.load_canonical_json_bytes(
        staged_raw,
        "Pre-publication staged section pinner",
    )
    state_record = pinner_state_record(staged_pinner, staged_raw)
    section = staged_pinner["section"]
    if _policy.SECTION_RE.fullmatch(section) is None or section not in progress.get("sections", []):
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Pre-publication staged pinner section is absent from the frozen manifest.",
            section=section,
        )
    pinner_path = state_record["pinner_path"]
    pinner_parts = _secure_io._relative_parts(pinner_path)
    if len(pinner_parts) != 2 or pinner_parts[0] != "pinners":
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Pre-publication staged pinner path is not an immediate content-addressed pinners child.",
        )
    _pinners.verify_section_pinner_bytes(
        root_fd,
        config,
        section,
        state_record,
        staged_pinner,
        staged_raw,
    )
    completed = state.get("completed_sections", {})
    dependencies = _sections.dependency_graph(_storage.absolute_path_no_follow(config["planning_dir"]), progress)
    expected_predecessors: list[dict[str, str]] = []
    for predecessor in dependencies.get(section, []):
        predecessor_record = completed.get(predecessor)
        if not isinstance(predecessor_record, dict):
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Pre-publication staged pinner is missing a current completed predecessor.",
                section=section,
                predecessor_section=predecessor,
            )
        expected_predecessors.append(
            {
                "section": predecessor,
                "pinner_path": predecessor_record["pinner_path"],
                "pinner_file_sha256": predecessor_record["pinner_file_sha256"],
            }
        )
    if staged_pinner.get("predecessor_pinners") != expected_predecessors:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Pre-publication staged pinner predecessor rows do not equal current state pointers.",
            section=section,
        )
    if state.get("completed_sections", {}).get(section) == state_record:
        raise _models.DetachedImplementationError(
            "section-record-recovery-required",
            "Pre-publication transaction residue cannot be cleaned from its candidate state.",
        )
    pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
    try:
        final_present = _transaction_io.section_record_entry_stat(pinners_fd, pinner_parts[1]) is not None
    finally:
        os.close(pinners_fd)
    if final_present:
        final_raw, final_stat = _transaction_io.read_regular_at_allow_links(
            root_fd,
            pinner_path,
            allowed_link_counts={1},
        )
        if (
            final_raw != staged_raw
            or _secure_io._fd_identity_from_stat(final_stat) == _secure_io._fd_identity_from_stat(staged_stat)
        ):
            raise _models.DetachedImplementationError(
                "section-record-recovery-required",
                "Pre-publication staged pinner does not reopen against an exact distinct orphan final.",
            )
    return staged_pinner, state_record, staged_raw
