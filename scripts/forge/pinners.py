"""Forge pinners."""

from __future__ import annotations

from typing import Any

from . import detached_contract as _detached_contract
from . import detached_state as _detached_state
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import policy as _policy
from . import sections as _sections
from . import secure_io as _secure_io
from . import storage as _storage

def verify_section_pinner_bytes(
    root_fd: int,
    config: dict[str, Any],
    section: str,
    state_record: dict[str, Any],
    pinner: dict[str, Any],
    raw: bytes,
    *,
    verify_predecessors: bool = True,
) -> tuple[dict[str, Any], str]:
    if not isinstance(state_record, dict):
        raise _models.DetachedImplementationError("invalid-detached-state", f"State record is not an object: {section}", section=section)
    _handoff_wire.require_exact_fields(state_record, _policy.PINNER_STATE_RECORD_FIELDS, f"State record for {section}")
    pinner_path = state_record.get("pinner_path")
    if not isinstance(pinner_path, str):
        raise _models.DetachedImplementationError("invalid-detached-state", f"State pinner path is invalid: {section}", section=section)
    file_sha256 = _handoff_wire.sha256_digest(raw)
    if file_sha256 != state_record.get("pinner_file_sha256"):
        raise _models.DetachedImplementationError(
            "pinner-drift",
            f"Section pinner hash no longer matches state: {section}",
            section=section,
            expected_pinner_file_sha256=state_record.get("pinner_file_sha256"),
            actual_pinner_file_sha256=file_sha256,
        )
    expected_pinner_path = f"pinners/{section}-{file_sha256.removeprefix('sha256:')}.json"
    if pinner_path != expected_pinner_path:
        raise _models.DetachedImplementationError(
            "invalid-section-pinner",
            f"Section pinner path is not content-addressed by its canonical file hash: {section}",
            section=section,
            expected_pinner_path=expected_pinner_path,
            actual_pinner_path=pinner_path,
        )
    _handoff_wire.require_exact_fields(pinner, _detached_contract.SECTION_PINNER_FIELDS, f"Section pinner for {section}")
    state_projection = {
        field: pinner.get(field)
        for field in _policy.PINNER_STATE_RECORD_FIELDS
        if field not in {"pinner_path", "pinner_file_sha256"}
    }
    expected_state_projection = {
        field: state_record.get(field)
        for field in _policy.PINNER_STATE_RECORD_FIELDS
        if field not in {"pinner_path", "pinner_file_sha256"}
    }
    if (
        pinner.get("schema") != _detached_contract.SECTION_PINNER_SCHEMA
        or pinner.get("section") != section
        or pinner.get("planning_tree_sha256") != config.get("planning_tree_sha256")
        or pinner.get("admission_pinner_sha256") != config.get("admission_pinner_sha256")
        or pinner.get("admission_state_sha256") != config.get("admission_state_sha256")
        or pinner.get("detached_implementation_root_identity_digest")
        != config.get("detached_implementation_root_identity_digest")
        or pinner.get("target_root_identity_digest") != config.get("target_root_identity_digest")
        or pinner.get("implement_tool_sha256") != config.get("implement_tool_sha256")
        or pinner.get("implement_skill_sha256") != config.get("implement_skill_sha256")
        or pinner.get("implement_test_sha256") != config.get("implement_test_sha256")
        or state_projection != expected_state_projection
    ):
        raise _models.DetachedImplementationError(
            "invalid-section-pinner",
            f"Section pinner is not bound to its section, planning tree, admission pinner, and implementation sources: {section}",
            section=section,
        )
    predecessor_rows = pinner.get("predecessor_pinners")
    if not isinstance(predecessor_rows, list):
        raise _models.DetachedImplementationError("invalid-section-pinner", f"Predecessor pinners are invalid: {section}", section=section)
    if verify_predecessors:
        for row in predecessor_rows:
            if not isinstance(row, dict) or set(row) != {"section", "pinner_path", "pinner_file_sha256"}:
                raise _models.DetachedImplementationError("invalid-section-pinner", f"Predecessor pinner row is invalid: {section}", section=section)
            if not all(isinstance(row.get(field), str) for field in ("section", "pinner_path", "pinner_file_sha256")):
                raise _models.DetachedImplementationError("invalid-section-pinner", f"Predecessor pinner row types are invalid: {section}", section=section)
            predecessor, predecessor_raw = _secure_io.load_canonical_json_at(root_fd, row["pinner_path"])
            predecessor_sha256 = _handoff_wire.sha256_digest(predecessor_raw)
            expected_predecessor_path = (
                f"pinners/{row['section']}-{predecessor_sha256.removeprefix('sha256:')}.json"
            )
            _handoff_wire.require_exact_fields(predecessor, _detached_contract.SECTION_PINNER_FIELDS, f"Predecessor pinner for {row['section']}")
            if (
                predecessor_sha256 != row["pinner_file_sha256"]
                or row["pinner_path"] != expected_predecessor_path
                or predecessor.get("schema") != _detached_contract.SECTION_PINNER_SCHEMA
                or predecessor.get("section") != row["section"]
                or predecessor.get("planning_tree_sha256") != config.get("planning_tree_sha256")
                or predecessor.get("admission_pinner_sha256") != config.get("admission_pinner_sha256")
                or predecessor.get("admission_state_sha256") != config.get("admission_state_sha256")
                or predecessor.get("detached_implementation_root_identity_digest")
                != config.get("detached_implementation_root_identity_digest")
                or predecessor.get("target_root_identity_digest") != config.get("target_root_identity_digest")
                or predecessor.get("implement_tool_sha256") != config.get("implement_tool_sha256")
                or predecessor.get("implement_skill_sha256") != config.get("implement_skill_sha256")
                or predecessor.get("implement_test_sha256") != config.get("implement_test_sha256")
            ):
                raise _models.DetachedImplementationError(
                    "predecessor-pinner-drift",
                    f"Predecessor pinner did not reopen with its recorded canonical file hash: {row.get('section')}",
                    section=section,
                    predecessor_section=row.get("section"),
                )
    return pinner, file_sha256


def verify_section_pinner(
    root_fd: int,
    config: dict[str, Any],
    section: str,
    state_record: dict[str, Any],
    *,
    verify_predecessors: bool = True,
) -> tuple[dict[str, Any], str]:
    if not isinstance(state_record, dict):
        raise _models.DetachedImplementationError(
            "invalid-detached-state",
            f"State record is not an object: {section}",
            section=section,
        )
    pinner_path = state_record.get("pinner_path")
    if not isinstance(pinner_path, str):
        raise _models.DetachedImplementationError(
            "invalid-detached-state",
            f"State pinner path is invalid: {section}",
            section=section,
        )
    pinner, raw = _secure_io.load_canonical_json_at(root_fd, pinner_path)
    return verify_section_pinner_bytes(
        root_fd,
        config,
        section,
        state_record,
        pinner,
        raw,
        verify_predecessors=verify_predecessors,
    )


def detached_completed_records(
    root_fd: int,
    config: dict[str, Any],
    progress: dict[str, Any],
    *,
    pending_pinner: tuple[str, dict[str, Any], bytes] | None = None,
) -> dict[str, dict[str, Any]]:
    state = _detached_state.load_detached_state(root_fd, config)
    completed = state["completed_sections"]
    known = set(progress.get("sections", []))
    unknown = sorted(set(completed) - known)
    if unknown:
        raise _models.DetachedImplementationError(
            "unknown-recorded-sections",
            "Detached state contains sections absent from the manifest.",
            unknown_recorded_sections=unknown,
        )
    reopened_pinners: dict[str, dict[str, Any]] = {}
    for section, record in completed.items():
        if pending_pinner is not None and pending_pinner[0] == section:
            pinner, _ = verify_section_pinner_bytes(
                root_fd,
                config,
                section,
                record,
                pending_pinner[1],
                pending_pinner[2],
            )
        else:
            pinner, _ = verify_section_pinner(root_fd, config, section, record)
        reopened_pinners[section] = pinner
    dependencies = _sections.dependency_graph(_storage.absolute_path_no_follow(config["planning_dir"]), progress)
    for section, pinner in reopened_pinners.items():
        expected_rows: list[dict[str, str]] = []
        for predecessor in dependencies.get(section, []):
            predecessor_record = completed.get(predecessor)
            if predecessor_record is None:
                raise _models.DetachedImplementationError(
                    "predecessor-pinner-current-state-mismatch",
                    f"Completed section no longer has a completed current predecessor: {section}",
                    section=section,
                    predecessor_section=predecessor,
                )
            expected_rows.append(
                {
                    "section": predecessor,
                    "pinner_path": predecessor_record["pinner_path"],
                    "pinner_file_sha256": predecessor_record["pinner_file_sha256"],
                }
            )
        if pinner["predecessor_pinners"] != expected_rows:
            actual_by_section = {
                row.get("section"): row
                for row in pinner["predecessor_pinners"]
                if isinstance(row, dict) and isinstance(row.get("section"), str)
            }
            mismatched = next(
                (
                    row["section"]
                    for row in expected_rows
                    if actual_by_section.get(row["section"]) != row
                ),
                None,
            )
            raise _models.DetachedImplementationError(
                "predecessor-pinner-current-state-mismatch",
                f"Completed section predecessor rows do not equal the current state pointers: {section}",
                section=section,
                predecessor_section=mismatched,
                expected_predecessor_pinners=expected_rows,
                actual_predecessor_pinners=pinner["predecessor_pinners"],
            )
    return completed


def completed_transitive_dependants(
    section: str,
    dependencies: dict[str, list[str]],
    completed: set[str],
) -> list[str]:
    discovered: set[str] = set()
    frontier = [section]
    while frontier:
        predecessor = frontier.pop()
        for candidate, candidate_dependencies in dependencies.items():
            if candidate in completed and candidate not in discovered and predecessor in candidate_dependencies:
                discovered.add(candidate)
                frontier.append(candidate)
    discovered.discard(section)
    return sorted(discovered)
