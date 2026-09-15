"""Forge detached authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import re

from . import authority as _authority
from . import detached_contract as _detached_contract
from . import detached_state as _detached_state
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import planning_snapshot as _planning_snapshot
from . import secure_io as _secure_io
from . import sources as _sources
from . import storage as _storage

def load_detached_config(root_fd: int, planning_dir: Path, implementation_root: Path) -> dict[str, Any]:
    config, _ = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_config.json")
    _handoff_wire.require_exact_fields(config, _detached_contract.DETACHED_CONFIG_FIELDS, "Detached implementation config")
    if config.get("schema") != _detached_contract.DETACHED_CONFIG_SCHEMA or config.get("mode") != "detached-frozen":
        raise _models.DetachedImplementationError(
            "invalid-detached-config",
            "Detached implementation config schema or mode is invalid.",
        )
    if not isinstance(config.get("target_root_identity_digest"), str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}",
        config["target_root_identity_digest"],
    ):
        raise _models.DetachedImplementationError(
            "invalid-detached-config",
            "Detached implementation config target root identity digest is invalid.",
        )
    expected_paths = {
        "planning_dir": str(_storage.absolute_path_no_follow(planning_dir)),
        "implementation_root": str(_storage.absolute_path_no_follow(implementation_root)),
        "state_path": str(_storage.absolute_path_no_follow(implementation_root) / "zagrosi_implement_state.json"),
        "progress_path": str(_storage.absolute_path_no_follow(implementation_root) / "forge-progress.json"),
        "reviews_dir": str(_storage.absolute_path_no_follow(implementation_root) / "code_review"),
        "evidence_dir": str(_storage.absolute_path_no_follow(implementation_root) / "evidence"),
        "pinners_dir": str(_storage.absolute_path_no_follow(implementation_root) / "pinners"),
        **{f"implement_{source}_path": str(path) for source, path in _sources.implementation_source_paths().items()},
    }
    mismatches = {key: {"expected": value, "actual": config.get(key)} for key, value in expected_paths.items() if config.get(key) != value}
    if mismatches:
        raise _models.DetachedImplementationError(
            "invalid-detached-config",
            "Detached implementation config path binding is invalid.",
            path_mismatches=mismatches,
        )
    return config


def reopen_detached_config_exact(root_fd: int, expected_config: dict[str, Any]) -> None:
    reopened, reopened_raw = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_config.json")
    _handoff_wire.require_exact_fields(reopened, _detached_contract.DETACHED_CONFIG_FIELDS, "Detached implementation config")
    expected_raw = _handoff_wire.canonical_json_bytes(expected_config)
    if reopened != expected_config or reopened_raw != expected_raw:
        raise _models.DetachedImplementationError(
            "detached-config-drift",
            "Detached implementation config bytes changed after the command opened its authority context.",
        )


def verify_target_root_authority(
    planning_dir: Path,
    planning_fd: int,
    implementation_root: Path,
    root_fd: int,
    config: dict[str, Any],
) -> None:
    target_dir = _storage.absolute_path_no_follow(config["target_dir"])
    if str(target_dir) != config["target_dir"]:
        raise _models.DetachedImplementationError(
            "invalid-detached-config",
            "Detached config target root is not an exact absolute no-follow path.",
        )
    target_fd = _secure_io.open_directory_chain_no_follow(target_dir)
    try:
        target_identity = _secure_io._fd_identity(target_fd)
        actual_digest = _authority.target_root_identity_digest(target_fd)
        if actual_digest != config.get("target_root_identity_digest"):
            raise _models.DetachedImplementationError(
                "target-root-identity-drift",
                "Protected target root identity no longer matches implement-setup.",
                expected_target_root_identity_digest=config.get("target_root_identity_digest"),
                actual_target_root_identity_digest=actual_digest,
            )
        _authority.require_planning_target_disjoint(planning_dir, planning_fd, target_dir, target_fd)
        _authority.require_open_roots_disjoint(implementation_root, root_fd, target_dir, target_fd)
        reopened_fd = _secure_io.open_directory_chain_no_follow(target_dir)
        try:
            if _secure_io._fd_identity(reopened_fd) != target_identity or _authority.target_root_identity_digest(reopened_fd) != actual_digest:
                raise _models.DetachedImplementationError(
                    "target-root-replaced",
                    "Protected target root changed while its cross-invocation identity was verified.",
                )
        finally:
            os.close(reopened_fd)
    finally:
        os.close(target_fd)


def verify_detached_authorities(
    planning_dir: Path,
    implementation_root: Path,
    root_fd: int,
    config: dict[str, Any],
    guard: _planning_snapshot.FrozenPlanningTree,
) -> None:
    reopen_detached_config_exact(root_fd, config)
    guard.verify_unchanged()
    _authority.require_planning_implementation_disjoint(
        planning_dir,
        guard.root_fd,
        implementation_root,
        root_fd,
    )
    reopened_root_fd = _secure_io.open_directory_chain_no_follow(implementation_root)
    try:
        if _secure_io._fd_identity(reopened_root_fd) != _secure_io._fd_identity(root_fd):
            raise _models.DetachedImplementationError(
                "detached-root-replaced",
                "Detached implementation root path no longer names the held authority descriptor.",
            )
        _authority.require_planning_implementation_disjoint(
            planning_dir,
            guard.root_fd,
            implementation_root,
            reopened_root_fd,
        )
    finally:
        os.close(reopened_root_fd)
    verify_target_root_authority(planning_dir, guard.root_fd, implementation_root, root_fd, config)
    actual_root_identity_digest = _detached_state.detached_implementation_root_identity_digest(root_fd)
    if config.get("detached_implementation_root_identity_digest") != actual_root_identity_digest:
        raise _models.DetachedImplementationError(
            "detached-root-identity-drift",
            "Detached implementation root identity changed after implement-setup.",
            expected_detached_implementation_root_identity_digest=config.get(
                "detached_implementation_root_identity_digest"
            ),
            actual_detached_implementation_root_identity_digest=actual_root_identity_digest,
        )
    _sources.verify_implementation_sources(config)
    _, _, _, admission_state_sha256 = _authority.reopen_admission_pinner(
        planning_dir,
        implementation_root,
        str(config["admission_pinner_path"]),
        expected_sha256=str(config["admission_pinner_sha256"]),
        planning_root_fd=guard.root_fd,
        implementation_root_fd=root_fd,
    )
    if config.get("admission_state_sha256") != admission_state_sha256:
        raise _models.DetachedImplementationError(
            "admission-state-drift",
            "Detached config admission state no longer equals the reopened final pinner START/END A digest.",
            expected_admission_state_sha256=config.get("admission_state_sha256"),
            actual_admission_state_sha256=admission_state_sha256,
        )
    reopen_detached_config_exact(root_fd, config)
    verify_target_root_authority(planning_dir, guard.root_fd, implementation_root, root_fd, config)
    guard.verify_unchanged()


def recover_planning_dir_from_detached_root(raw_implementation_root: str) -> Path:
    implementation_root = _storage.absolute_path_no_follow(raw_implementation_root)
    root_fd = _secure_io.open_directory_chain_no_follow(implementation_root)
    try:
        _detached_state.detached_implementation_root_identity_digest(
            root_fd,
            allow_recoverable_temps=True,
        )
        config, _ = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_config.json")
        _handoff_wire.require_exact_fields(config, _detached_contract.DETACHED_CONFIG_FIELDS, "Detached implementation config")
        planning_dir = config.get("planning_dir")
        if (
            config.get("schema") != _detached_contract.DETACHED_CONFIG_SCHEMA
            or config.get("mode") != "detached-frozen"
            or config.get("implementation_root") != str(implementation_root)
            or type(planning_dir) is not str
            or str(_storage.absolute_path_no_follow(planning_dir)) != planning_dir
        ):
            raise _models.DetachedImplementationError(
                "invalid-detached-config",
                "Detached config cannot authoritatively recover its planning root.",
            )
        return Path(planning_dir)
    finally:
        os.close(root_fd)
