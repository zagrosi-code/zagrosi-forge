"""Forge detached state."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import os
import re
import stat

from . import detached_contract as _detached_contract
from . import handoff_wire as _handoff_wire
from . import markdown as _markdown
from . import models as _models
from . import planning_snapshot as _planning_snapshot
from . import secure_io as _secure_io
from . import storage as _storage

def detached_setup_prefix_payload(
    slot: str,
    *,
    planning_dir: Path,
    sections_dir: Path,
    target_dir: Path,
    target_root_identity_digest: str,
    implementation_root: Path,
    guard: _planning_snapshot.FrozenPlanningTree,
    admission_path: Path,
    admission_sha256: str,
    admission_size: int,
    admission_state_sha256: str,
    source_records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    without_self = {
        "schema": _detached_contract.DETACHED_SETUP_PREFIX_SCHEMA,
        "slot": slot,
        "planning_dir": str(planning_dir),
        "sections_dir": str(sections_dir),
        "target_dir": str(target_dir),
        "target_root_identity_digest": target_root_identity_digest,
        "implementation_root": str(implementation_root),
        "planning_tree_sha256": guard.digest,
        "planning_file_count": guard.file_count,
        "planning_total_bytes": guard.total_bytes,
        "admission_pinner_path": str(admission_path),
        "admission_pinner_sha256": admission_sha256,
        "admission_pinner_size": admission_size,
        "admission_state_sha256": admission_state_sha256,
        "implement_tool_sha256": source_records["tool"]["sha256"],
        "implement_skill_sha256": source_records["skill"]["sha256"],
        "implement_test_sha256": source_records["test"]["sha256"],
    }
    payload = {
        **without_self,
        "self_digest": _handoff_wire.domain_sha256(
            b"zagrosi-detached-implementation-setup-prefix-v2-self\0",
            _handoff_wire.canonical_json_bytes(without_self),
        ),
    }
    _handoff_wire.require_exact_fields(payload, _detached_contract.DETACHED_SETUP_PREFIX_FIELDS, f"Detached setup prefix for {slot}")
    return payload


def ensure_detached_root_file_slot(
    root_fd: int,
    relative: str,
    pending_payload: dict[str, Any],
) -> tuple[bool, dict[str, Any], bytes]:
    parent_fd, name = _secure_io.open_relative_parent(root_fd, relative)
    file_fd: int | None = None
    temporary = f".{name}.setup.tmp"
    pending_raw = _handoff_wire.canonical_json_bytes(pending_payload)
    try:
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            existing, existing_raw = _secure_io.load_canonical_json_at(root_fd, relative)
            return False, existing, existing_raw
        file_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        _secure_io._write_all(file_fd, pending_raw)
        os.fsync(file_fd)
        os.close(file_fd)
        file_fd = None
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise _models.DetachedImplementationError(
                "detached-setup-prefix-conflict",
                f"Detached setup slot appeared before atomic publication: {relative}",
            )
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True, pending_payload, pending_raw
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def require_detached_top_level_inventory(
    root_fd: int,
    *,
    complete: bool,
    allow_recoverable_temps: bool = False,
) -> None:
    first = set(os.listdir(root_fd))
    allowed = _detached_contract.DETACHED_TOP_LEVEL_ALLOWED | (
        _detached_contract.DETACHED_ROOT_RECOVERABLE_TEMPS if allow_recoverable_temps else set()
    )
    unknown = sorted(first - allowed)
    missing = sorted(_detached_contract.DETACHED_TOP_LEVEL_ALLOWED - first) if complete else []
    if unknown or missing:
        raise _models.DetachedImplementationError(
            "unsafe-detached-root-inventory",
            "Detached implementation root must contain exactly its six fixed top-level members.",
            unknown_top_level_members=unknown,
            missing_top_level_members=missing,
        )
    for name in sorted(first):
        observed = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if name in _detached_contract.DETACHED_TOP_LEVEL_DIRECTORIES:
            valid = (
                stat.S_ISDIR(observed.st_mode)
                and stat.S_IMODE(observed.st_mode) == 0o700
                and observed.st_uid == os.getuid()
            )
        else:
            valid = (
                stat.S_ISREG(observed.st_mode)
                and stat.S_IMODE(observed.st_mode) == 0o600
                and observed.st_uid == os.getuid()
                and observed.st_nlink == 1
                and (
                    name not in _detached_contract.DETACHED_ROOT_RECOVERABLE_TEMPS
                    or observed.st_size <= _detached_contract.DETACHED_JSON_CAP
                )
            )
        if not valid:
            raise _models.DetachedImplementationError(
                "unsafe-detached-root-inventory",
                "Detached implementation root top-level member metadata is unsafe.",
                top_level_member=name,
            )
    if set(os.listdir(root_fd)) != first:
        raise _models.DetachedImplementationError(
            "unsafe-detached-root-inventory",
            "Detached implementation root inventory changed while it was verified.",
        )


def recover_detached_root_temps_locked(root_fd: int) -> None:
    inventory = set(os.listdir(root_fd))
    unknown = sorted(inventory - _detached_contract.DETACHED_TOP_LEVEL_ALLOWED - _detached_contract.DETACHED_ROOT_RECOVERABLE_TEMPS)
    if unknown:
        raise _models.DetachedImplementationError(
            "unsafe-detached-root-inventory",
            "Detached implementation root contains unknown members; no recovery mutation was attempted.",
            unknown_top_level_members=unknown,
        )
    present = sorted(inventory & _detached_contract.DETACHED_ROOT_RECOVERABLE_TEMPS)
    for name in present:
        observed = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(observed.st_mode)
            or stat.S_IMODE(observed.st_mode) != 0o600
            or observed.st_uid != os.getuid()
            or observed.st_nlink != 1
            or observed.st_size > _detached_contract.DETACHED_JSON_CAP
        ):
            raise _models.DetachedImplementationError(
                "unsafe-detached-root-temp",
                "Recoverable detached root temp has unsafe metadata and was retained.",
                temp_name=name,
            )
    if set(os.listdir(root_fd)) != inventory:
        raise _models.DetachedImplementationError(
            "unsafe-detached-root-inventory",
            "Detached root inventory changed during locked temp recovery.",
        )
    for name in present:
        os.unlink(name, dir_fd=root_fd)
        os.fsync(root_fd)


def detached_implementation_root_identity_digest(
    root_fd: int,
    *,
    require_fixed_children: bool = True,
    allow_recoverable_temps: bool = False,
) -> str:
    observed = os.fstat(root_fd)
    mode = stat.S_IMODE(observed.st_mode)
    if not stat.S_ISDIR(observed.st_mode) or mode != 0o700 or observed.st_uid != os.getuid():
        raise _models.DetachedImplementationError(
            "unsafe-detached-root-identity",
            "Detached implementation root must be a user-owned 0700 directory.",
            expected_uid=os.getuid(),
            actual_uid=observed.st_uid,
            expected_mode=0o700,
            actual_mode=mode,
        )
    require_detached_top_level_inventory(
        root_fd,
        complete=require_fixed_children,
        allow_recoverable_temps=allow_recoverable_temps,
    )
    if require_fixed_children:
        for relative in ("code_review", "evidence", "pinners"):
            child_fd = _secure_io.open_relative_directory(root_fd, relative)
            try:
                child = os.fstat(child_fd)
                if (
                    not stat.S_ISDIR(child.st_mode)
                    or stat.S_IMODE(child.st_mode) != 0o700
                    or child.st_uid != os.getuid()
                ):
                    raise _models.DetachedImplementationError(
                        "unsafe-detached-root-identity",
                        "Detached implementation fixed child directories must be user-owned 0700 directories.",
                        child=relative,
                    )
            finally:
                os.close(child_fd)
    identity = {
        "device": observed.st_dev,
        "gid": observed.st_gid,
        "inode": observed.st_ino,
        "link_count": observed.st_nlink,
        "mode": mode,
        "uid": observed.st_uid,
    }
    digest = hashlib.sha256(b"zagrosi-detached-implementation-root-identity-v1\0")
    digest.update(_handoff_wire.handoff_canonical_json_body(identity))
    return "sha256:" + digest.hexdigest()


def require_detached_root_identity_through_recoverable_temps(
    root_fd: int,
    expected_digest: Any,
) -> None:
    if not isinstance(expected_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest):
        raise _models.DetachedImplementationError(
            "invalid-detached-config",
            "Detached config implementation-root identity digest is invalid.",
        )
    actual_digest = detached_implementation_root_identity_digest(
        root_fd,
        allow_recoverable_temps=True,
    )
    if actual_digest == expected_digest:
        return
    inventory = set(os.listdir(root_fd))
    temp_count = len(inventory & _detached_contract.DETACHED_ROOT_RECOVERABLE_TEMPS)
    observed = os.fstat(root_fd)
    if temp_count and observed.st_nlink >= temp_count:
        identity = {
            "device": observed.st_dev,
            "gid": observed.st_gid,
            "inode": observed.st_ino,
            "link_count": observed.st_nlink - temp_count,
            "mode": stat.S_IMODE(observed.st_mode),
            "uid": observed.st_uid,
        }
        digest = hashlib.sha256(b"zagrosi-detached-implementation-root-identity-v1\0")
        digest.update(_handoff_wire.handoff_canonical_json_body(identity))
        if "sha256:" + digest.hexdigest() == expected_digest:
            return
    raise _models.DetachedImplementationError(
        "detached-root-identity-drift",
        "Detached implementation root identity no longer matches implement-setup.",
        expected_detached_implementation_root_identity_digest=expected_digest,
        actual_detached_implementation_root_identity_digest=actual_digest,
    )


def detached_state_default(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": _detached_contract.DETACHED_STATE_SCHEMA,
        "mode": "detached-frozen",
        "planning_tree_sha256": config["planning_tree_sha256"],
        "admission_pinner_sha256": config["admission_pinner_sha256"],
        "admission_state_sha256": config["admission_state_sha256"],
        "detached_implementation_root_identity_digest": config[
            "detached_implementation_root_identity_digest"
        ],
        "target_root_identity_digest": config["target_root_identity_digest"],
        "created_at": _storage.now_iso(),
        "completed_sections": {},
    }


def detached_progress_default(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": _detached_contract.DETACHED_PROGRESS_SCHEMA,
        "mode": "detached-frozen",
        "planning_tree_sha256": config["planning_tree_sha256"],
        "admission_pinner_sha256": config["admission_pinner_sha256"],
        "created_at": _storage.now_iso(),
        "events": [],
    }


def load_detached_progress(root_fd: int, config: dict[str, Any]) -> dict[str, Any]:
    progress, _ = _secure_io.load_canonical_json_at(root_fd, "forge-progress.json")
    _handoff_wire.require_exact_fields(progress, _detached_contract.DETACHED_PROGRESS_FIELDS, "Detached implementation progress")
    if (
        progress.get("schema") != _detached_contract.DETACHED_PROGRESS_SCHEMA
        or progress.get("mode") != "detached-frozen"
        or progress.get("planning_tree_sha256") != config.get("planning_tree_sha256")
        or progress.get("admission_pinner_sha256") != config.get("admission_pinner_sha256")
        or not isinstance(progress.get("created_at"), str)
        or not isinstance(progress.get("events"), list)
    ):
        raise _models.DetachedImplementationError(
            "invalid-detached-progress",
            "Detached progress is not bound to the current planning tree and admission pinner.",
        )
    return progress


def load_detached_state(root_fd: int, config: dict[str, Any]) -> dict[str, Any]:
    state, _ = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
    _handoff_wire.require_exact_fields(state, _detached_contract.DETACHED_STATE_FIELDS, "Detached implementation state")
    if (
        state.get("schema") != _detached_contract.DETACHED_STATE_SCHEMA
        or state.get("mode") != "detached-frozen"
        or state.get("planning_tree_sha256") != config.get("planning_tree_sha256")
        or state.get("admission_pinner_sha256") != config.get("admission_pinner_sha256")
        or state.get("admission_state_sha256") != config.get("admission_state_sha256")
        or state.get("detached_implementation_root_identity_digest")
        != config.get("detached_implementation_root_identity_digest")
        or state.get("target_root_identity_digest") != config.get("target_root_identity_digest")
        or not isinstance(state.get("completed_sections"), dict)
    ):
        raise _models.DetachedImplementationError(
            "invalid-detached-state",
            "Detached implementation state is not bound to the current config, planning tree, admission pinner, and target root.",
        )
    return state


def detached_artifact_relative(implementation_root: Path, raw_path: str) -> str:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        absolute = _storage.absolute_path_no_follow(candidate)
        try:
            candidate = absolute.relative_to(implementation_root)
        except ValueError as exc:
            raise _models.DetachedImplementationError(
                "external-artifact-outside-root",
                "Detached review and evidence artifacts must be inside the implementation root.",
                path=str(absolute),
            ) from exc
    parts = _secure_io._relative_parts(candidate.as_posix())
    return Path(*parts).as_posix()


def detached_review_rows(root_fd: int, implementation_root: Path, section: str, values: list[str]) -> list[dict[str, Any]]:
    relative_paths = sorted({detached_artifact_relative(implementation_root, value) for value in _markdown.normalize_repeated(values)})
    required = {
        f"code_review/{section}-review.md",
        f"code_review/{section}-decisions.md",
    }
    missing = sorted(required - set(relative_paths))
    if missing:
        raise _models.DetachedImplementationError(
            "missing-detached-review",
            "Detached record requires the section review and decisions artifacts.",
            missing_review_artifacts=missing,
        )
    rows: list[dict[str, Any]] = []
    for relative in relative_paths:
        raw = _secure_io.read_single_link_regular_at(root_fd, relative, cap=_detached_contract.DETACHED_REVIEW_CAP)
        rows.append({"path": relative, "sha256": _handoff_wire.sha256_digest(raw), "size": len(raw)})
    return rows


def detached_evidence_rows(root_fd: int, implementation_root: Path, values: list[str]) -> list[dict[str, Any]]:
    parsed: dict[str, str] = {}
    for value in _markdown.normalize_repeated(values):
        name, separator, raw_path = value.partition("=")
        if not separator or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise _models.DetachedImplementationError(
                "invalid-evidence-row",
                "Evidence rows use NAME=PATH with a unique lower-snake-case name.",
                evidence_row=value,
            )
        if name in parsed:
            raise _models.DetachedImplementationError(
                "invalid-evidence-row",
                f"Duplicate detached evidence row name: {name}",
                evidence_row=name,
            )
        parsed[name] = detached_artifact_relative(implementation_root, raw_path)
    rows: list[dict[str, Any]] = []
    for name in sorted(parsed):
        relative = parsed[name]
        payload, raw = _secure_io.load_canonical_json_at(root_fd, relative)
        if not payload.get("schema"):
            raise _models.DetachedImplementationError(
                "invalid-evidence-row",
                f"Detached evidence row must name a canonical JSON object with a schema: {name}",
                evidence_row=name,
            )
        if payload.get("schema") == "unit12-privileged-darwin-apfs-gate-result-v1":
            raise _models.DetachedImplementationError(
                "raw-privileged-evidence-forbidden",
                "Root-owned privileged gate results are never accepted as user-owned detached evidence rows.",
                evidence_row=name,
            )
        rows.append({"name": name, "path": relative, "sha256": _handoff_wire.sha256_digest(raw), "size": len(raw)})
    return rows


def detached_section_evidence_values(section: str, values: list[str]) -> list[str]:
    normalized = _markdown.normalize_repeated(values)
    reserved_names = {name for name, _ in _detached_contract.REQUIRED_PRIVILEGED_SECTION_EVIDENCE.values()}
    reserved_paths = {path for _, path in _detached_contract.REQUIRED_PRIVILEGED_SECTION_EVIDENCE.values()}
    for value in normalized:
        name, separator, raw_path = value.partition("=")
        if not separator:
            continue
        relative = Path(raw_path).as_posix() if not Path(raw_path).is_absolute() else raw_path
        if name in reserved_names or relative in reserved_paths:
            raise _models.DetachedImplementationError(
                "reserved-evidence-row",
                "Section 26 and Section 28 privileged handoff evidence rows are derived by Forge and cannot be caller supplied.",
                evidence_row=value,
            )
    required = _detached_contract.REQUIRED_PRIVILEGED_SECTION_EVIDENCE.get(section)
    if required is not None:
        normalized.append(f"{required[0]}={required[1]}")
    return normalized


def require_privileged_section_evidence(section: str, rows: list[dict[str, Any]]) -> None:
    required = _detached_contract.REQUIRED_PRIVILEGED_SECTION_EVIDENCE.get(section)
    if required is None:
        return
    required_name, required_path = required
    row_by_name = {row["name"]: row for row in rows}
    if required_name not in row_by_name:
        raise _models.DetachedImplementationError(
            "missing-required-section-evidence",
            f"Section requires its exact privileged Darwin/APFS evidence row: {section}",
            section=section,
            required_evidence_name=required_name,
            required_evidence_path=required_path,
        )
    actual_path = row_by_name[required_name]["path"]
    if actual_path != required_path:
        raise _models.DetachedImplementationError(
            "invalid-required-section-evidence",
            f"Privileged Darwin/APFS evidence row has the wrong fixed path: {section}",
            section=section,
            required_evidence_name=required_name,
            required_evidence_path=required_path,
            actual_evidence_path=actual_path,
        )


def require_verified_privileged_evidence_bytes(
    section: str,
    rows: list[dict[str, Any]],
    receipt_raw: bytes | None,
) -> None:
    required = _detached_contract.REQUIRED_PRIVILEGED_SECTION_EVIDENCE.get(section)
    if required is None:
        if receipt_raw is not None:
            raise _models.DetachedImplementationError(
                "invalid-required-section-evidence",
                "A privileged handoff receipt was returned for a section without that gate.",
                section=section,
            )
        return
    require_privileged_section_evidence(section, rows)
    assert receipt_raw is not None
    required_name, required_path = required
    row = next(item for item in rows if item["name"] == required_name)
    expected = {
        "name": required_name,
        "path": required_path,
        "sha256": _handoff_wire.sha256_digest(receipt_raw),
        "size": len(receipt_raw),
    }
    if row != expected:
        raise _models.DetachedImplementationError(
            "detached-evidence-drift",
            "Privileged handoff receipt bytes changed between verification and section pinner construction.",
            section=section,
            expected_evidence_row=expected,
            actual_evidence_row=row,
        )
