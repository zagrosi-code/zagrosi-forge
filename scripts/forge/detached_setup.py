"""Forge detached setup."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import Any
import argparse
import os
import re
import sys
import time

from . import authority as _authority
from . import detached_authority as _detached_authority
from . import detached_contract as _detached_contract
from . import detached_state as _detached_state
from . import flights as _flights
from . import gates as _gates
from . import handoff_wire as _handoff_wire
from . import locks as _locks
from . import models as _models
from . import output as _output
from . import pinners as _pinners
from . import planning_snapshot as _planning_snapshot
from . import recovery as _recovery
from . import sections as _sections
from . import secure_io as _secure_io
from . import sources as _sources
from . import storage as _storage
from . import validation as _validation

def detached_implement_setup(args: argparse.Namespace) -> int:
    sections_dir = _storage.absolute_path_no_follow(args.sections_dir)
    planning_dir = sections_dir.parent
    target_dir = _storage.absolute_path_no_follow(args.target_dir or os.getcwd())
    guard: _planning_snapshot.FrozenPlanningTree | None = None
    root_fd: int | None = None
    target_fd: int | None = None
    implementation_root: Path | None = None
    lock_context: ExitStack | None = None
    require_lock_authority = None
    try:
        if not sections_dir.exists() or not sections_dir.is_dir():
            return _output.print_json({"success": False, "error": f"Sections directory not found: {sections_dir}"}, 1)
        if not target_dir.exists() or not target_dir.is_dir():
            return _output.print_json({"success": False, "error": f"Target directory not found: {target_dir}"}, 1)
        target_fd = _secure_io.open_directory_chain_no_follow(target_dir)
        if not getattr(args, "admission_pinner", None):
            raise _models.DetachedImplementationError(
                "missing-admission-pinner",
                "Detached frozen-planning mode requires --admission-pinner.",
            )
        expected_admission_sha256 = getattr(args, "expected_admission_pinner_sha256", None)
        if not isinstance(expected_admission_sha256, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_admission_sha256):
            raise _models.DetachedImplementationError(
                "missing-admission-pinner-hash",
                "Detached frozen-planning mode requires --expected-admission-pinner-sha256 with an exact sha256 digest.",
            )
        guard = _planning_snapshot.FrozenPlanningTree.open(planning_dir)
        _authority.require_planning_target_disjoint(planning_dir, guard.root_fd, target_dir, target_fd)
        setup_target_identity = _secure_io._fd_identity(target_fd)
        setup_target_identity_digest = _authority.target_root_identity_digest(target_fd)
        progress = _sections.check_section_progress(planning_dir)
        if progress["state"] in {"invalid_index", "no_index"}:
            return _output.print_json({"success": False, "section_progress": progress}, 1)
        artifact_payload = _validation.plan_artifacts_payload(planning_dir, argparse.Namespace(profile=args.profile, strict=True, allow_compact=False))
        if not artifact_payload["success"]:
            artifact_payload["error"] = "Forge planning process is incomplete; finish zagrosi-plan before implementation."
            return _output.print_json(artifact_payload, 1)

        lock_deadline = time.monotonic() + _detached_contract.DETACHED_LOCK_TIMEOUT_SECONDS
        lock_context = ExitStack()
        require_global_authority = lock_context.enter_context(_locks.detached_global_lock(lock_deadline))
        require_global_authority()
        requested_root = _storage.absolute_path_no_follow(args.implementation_root)
        admission_path, admission_sha256, admission_size, admission_state_sha256 = _authority.reopen_admission_pinner(
            planning_dir,
            requested_root,
            args.admission_pinner,
            expected_sha256=expected_admission_sha256,
            planning_root_fd=guard.root_fd,
        )
        expected_source_hashes = _sources.expected_implementation_source_hashes(args)
        source_records = _sources.reopen_implementation_sources(expected_hashes=expected_source_hashes)
        guard.verify_unchanged()
        _authority.require_candidate_root_disjoint_from_directory(requested_root, target_dir, target_fd)
        implementation_root, root_fd = _authority.ensure_detached_root(
            planning_dir,
            args.implementation_root,
            create=True,
            planning_root_fd=guard.root_fd,
        )
        require_root_authority = lock_context.enter_context(
            _locks.section_record_lock(
                root_fd,
                implementation_root,
                timeout_seconds=max(0.0, lock_deadline - time.monotonic()),
                create_marker_parent=True,
                defer_marker=True,
            )
        )

        def require_lock_authority(
            *,
            create_marker: bool = False,
            require_marker: bool = False,
        ) -> None:
            require_global_authority()
            require_root_authority(
                create_marker=create_marker,
                require_marker=require_marker,
            )
            require_global_authority()

        require_lock_authority()
        _detached_state.require_detached_top_level_inventory(
            root_fd,
            complete=False,
            allow_recoverable_temps=True,
        )
        _authority.require_planning_implementation_disjoint(planning_dir, guard.root_fd, implementation_root, root_fd)
        _authority.require_planning_target_disjoint(planning_dir, guard.root_fd, target_dir, target_fd)
        _authority.require_open_roots_disjoint(implementation_root, root_fd, target_dir, target_fd)
        reopened_target_fd = _secure_io.open_directory_chain_no_follow(target_dir)
        try:
            if (
                _secure_io._fd_identity(reopened_target_fd) != setup_target_identity
                or _authority.target_root_identity_digest(reopened_target_fd) != setup_target_identity_digest
            ):
                raise _models.DetachedImplementationError(
                    "target-root-replaced",
                    "Protected target root changed during detached implement-setup.",
                )
        finally:
            os.close(reopened_target_fd)
        admission_path, admission_sha256, admission_size, admission_state_sha256 = _authority.reopen_admission_pinner(
            planning_dir,
            implementation_root,
            args.admission_pinner,
            expected_sha256=expected_admission_sha256,
            planning_root_fd=guard.root_fd,
            implementation_root_fd=root_fd,
        )
        guard.verify_unchanged()
        _sources.verify_implementation_sources(
            {
                **_sources.implementation_source_config_fields(source_records),
            }
        )
        _detached_state.detached_implementation_root_identity_digest(
            root_fd,
            require_fixed_children=False,
            allow_recoverable_temps=True,
        )
        pending_by_path: dict[str, dict[str, Any]] = {}
        for relative, slot in (
            ("zagrosi_implement_config.json", "config"),
            ("zagrosi_implement_state.json", "state"),
            ("forge-progress.json", "progress"),
        ):
            pending = _detached_state.detached_setup_prefix_payload(
                slot,
                planning_dir=planning_dir,
                sections_dir=sections_dir,
                target_dir=target_dir,
                target_root_identity_digest=setup_target_identity_digest,
                implementation_root=implementation_root,
                guard=guard,
                admission_path=admission_path,
                admission_sha256=admission_sha256,
                admission_size=admission_size,
                admission_state_sha256=admission_state_sha256,
                source_records=source_records,
            )
            pending_by_path[relative] = pending

        def setup_config_payload(root_identity_digest: str) -> dict[str, Any]:
            payload = {
                "schema": _detached_contract.DETACHED_CONFIG_SCHEMA,
                "mode": "detached-frozen",
                "planning_dir": str(planning_dir),
                "sections_dir": str(sections_dir),
                "target_dir": str(target_dir),
                "target_root_identity_digest": setup_target_identity_digest,
                "implementation_root": str(implementation_root),
                "state_path": str(implementation_root / "zagrosi_implement_state.json"),
                "progress_path": str(implementation_root / "forge-progress.json"),
                "reviews_dir": str(implementation_root / "code_review"),
                "evidence_dir": str(implementation_root / "evidence"),
                "pinners_dir": str(implementation_root / "pinners"),
                "planning_tree_sha256": guard.digest,
                "planning_file_count": guard.file_count,
                "planning_total_bytes": guard.total_bytes,
                "admission_pinner_path": str(admission_path),
                "admission_pinner_sha256": admission_sha256,
                "admission_pinner_size": admission_size,
                "admission_state_sha256": admission_state_sha256,
                "detached_implementation_root_identity_digest": root_identity_digest,
                **_sources.implementation_source_config_fields(source_records),
                "test_command": progress.get("project_config", {}).get("test_command"),
                "runtime": progress.get("project_config", {}).get("runtime"),
            }
            _handoff_wire.require_exact_fields(payload, _detached_contract.DETACHED_CONFIG_FIELDS, "Detached implementation config")
            return payload

        existing_top_level = set(os.listdir(root_fd)) - _detached_contract.DETACHED_ROOT_RECOVERABLE_TEMPS
        existing_files = existing_top_level & _detached_contract.DETACHED_TOP_LEVEL_FILES
        config_name = "zagrosi_implement_config.json"
        if config_name not in existing_files and existing_files:
            raise _models.DetachedImplementationError(
                "detached-setup-prefix-conflict",
                "Detached setup cannot adopt state/progress slots without its exact authenticated config prefix.",
            )
        if config_name in existing_files:
            existing_config, _ = _secure_io.load_canonical_json_at(root_fd, config_name)
            if existing_config.get("schema") == _detached_contract.DETACHED_SETUP_PREFIX_SCHEMA:
                if existing_config != pending_by_path[config_name]:
                    raise _models.DetachedImplementationError(
                        "detached-setup-prefix-conflict",
                        "Existing detached setup prefix does not match the current authenticated setup inputs.",
                    )
                if not _detached_contract.DETACHED_TOP_LEVEL_DIRECTORIES.issubset(existing_top_level):
                    raise _models.DetachedImplementationError(
                        "detached-setup-prefix-conflict",
                        "An authenticated config prefix requires all fixed root directories to pre-exist.",
                    )
                allowed_pending_slot_sets = (
                    {config_name},
                    {config_name, "zagrosi_implement_state.json"},
                    set(_detached_contract.DETACHED_TOP_LEVEL_FILES),
                )
                if existing_files not in allowed_pending_slot_sets:
                    raise _models.DetachedImplementationError(
                        "detached-setup-prefix-conflict",
                        "Existing detached setup prefix slots violate config-to-state-to-progress publication order.",
                    )
                for relative in existing_files - {config_name}:
                    existing, _ = _secure_io.load_canonical_json_at(root_fd, relative)
                    if existing != pending_by_path[relative]:
                        raise _models.DetachedImplementationError(
                            "detached-setup-prefix-conflict",
                            "Existing detached setup prefix slots are not exact authenticated pending objects.",
                        )
                require_lock_authority(require_marker=True)
            elif existing_config.get("schema") == _detached_contract.DETACHED_CONFIG_SCHEMA:
                if existing_top_level != _detached_contract.DETACHED_TOP_LEVEL_ALLOWED:
                    raise _models.DetachedImplementationError(
                        "detached-config-conflict",
                        "A final detached config requires the complete exact six-member root before replay.",
                    )
                _detached_state.require_detached_root_identity_through_recoverable_temps(
                    root_fd,
                    existing_config.get("detached_implementation_root_identity_digest"),
                )
                expected_existing_config = setup_config_payload(
                    existing_config["detached_implementation_root_identity_digest"]
                )
                if existing_config != expected_existing_config:
                    raise _models.DetachedImplementationError(
                        "detached-config-conflict",
                        "Existing final detached config does not equal the complete current authenticated setup config.",
                    )
                existing_state, _ = _secure_io.load_canonical_json_at(root_fd, "zagrosi_implement_state.json")
                existing_progress, _ = _secure_io.load_canonical_json_at(root_fd, "forge-progress.json")
                state_pending_before_cleanup = (
                    existing_state == pending_by_path["zagrosi_implement_state.json"]
                )
                progress_pending_before_cleanup = (
                    existing_progress == pending_by_path["forge-progress.json"]
                )
                if not state_pending_before_cleanup:
                    _detached_state.load_detached_state(root_fd, existing_config)
                if not progress_pending_before_cleanup:
                    _detached_state.load_detached_progress(root_fd, existing_config)
                if (state_pending_before_cleanup, progress_pending_before_cleanup) not in {
                    (True, True),
                    (False, True),
                    (False, False),
                }:
                    raise _models.DetachedImplementationError(
                        "detached-setup-prefix-conflict",
                        "Final detached setup slots violate config-to-state-to-progress promotion order.",
                    )
                require_lock_authority(require_marker=True)
            else:
                raise _models.DetachedImplementationError(
                    "detached-setup-prefix-conflict",
                    "Detached setup refuses an arbitrary caller-planted config prefix.",
                )
        if config_name not in existing_files or existing_config.get("schema") == _detached_contract.DETACHED_SETUP_PREFIX_SCHEMA:
            for directory in ("code_review", "evidence"):
                if directory not in existing_top_level:
                    continue
                existing_directory_fd = _secure_io.open_relative_directory(root_fd, directory)
                try:
                    first_members = set(os.listdir(existing_directory_fd))
                    if first_members or set(os.listdir(existing_directory_fd)) != first_members:
                        raise _models.DetachedImplementationError(
                            "detached-setup-prefix-conflict",
                            "Fresh or pending detached setup requires empty review and evidence directories.",
                            directory=directory,
                        )
                finally:
                    os.close(existing_directory_fd)
            if "pinners" in existing_top_level:
                existing_pinners_fd = _secure_io.open_relative_directory(root_fd, "pinners")
                try:
                    first_pinner_members = set(os.listdir(existing_pinners_fd))
                    unexpected_pinner_members = sorted(
                        first_pinner_members - {Path(_detached_contract.SECTION_RECORD_LOCK_PATH).name}
                    )
                    if unexpected_pinner_members:
                        raise _models.DetachedImplementationError(
                            "detached-setup-prefix-conflict",
                            "Fresh or pending detached setup refuses pre-planted pinner members.",
                            unexpected_pinner_members=unexpected_pinner_members,
                        )
                    if set(os.listdir(existing_pinners_fd)) != first_pinner_members:
                        raise _models.DetachedImplementationError(
                            "detached-setup-prefix-conflict",
                            "Fresh or pending detached pinners inventory changed during authentication.",
                        )
                finally:
                    os.close(existing_pinners_fd)
        _detached_state.recover_detached_root_temps_locked(root_fd)
        _detached_state.require_detached_top_level_inventory(
            root_fd,
            complete=existing_config.get("schema") == _detached_contract.DETACHED_CONFIG_SCHEMA
            if config_name in existing_files
            else False,
        )
        require_lock_authority(create_marker=True)
        for relative in ("code_review", "evidence", "pinners"):
            directory_fd = _secure_io.open_relative_directory(root_fd, relative, create=True)
            os.close(directory_fd)
        observed_by_path: dict[str, dict[str, Any]] = {}
        for relative in (
            "zagrosi_implement_config.json",
            "zagrosi_implement_state.json",
            "forge-progress.json",
        ):
            _, observed, _ = _detached_state.ensure_detached_root_file_slot(
                root_fd,
                relative,
                pending_by_path[relative],
            )
            observed_by_path[relative] = observed
        root_identity_digest = _detached_state.detached_implementation_root_identity_digest(root_fd)

        config = setup_config_payload(root_identity_digest)
        observed_config = observed_by_path["zagrosi_implement_config.json"]
        observed_state = observed_by_path["zagrosi_implement_state.json"]
        observed_progress = observed_by_path["forge-progress.json"]
        config_pending = observed_config == pending_by_path["zagrosi_implement_config.json"]
        state_pending = observed_state == pending_by_path["zagrosi_implement_state.json"]
        progress_pending = observed_progress == pending_by_path["forge-progress.json"]
        config_final = observed_config == config
        state_final = False
        progress_final = False
        if not config_pending and not config_final:
            raise _models.DetachedImplementationError(
                "detached-config-conflict",
                "Existing detached config is neither the exact authenticated pending prefix nor this setup's final config.",
            )
        if config_pending and (not state_pending or not progress_pending):
            raise _models.DetachedImplementationError(
                "detached-setup-prefix-conflict",
                "Detached setup prefix slots are not in an exact recoverable creation order.",
            )
        if config_pending:
            _secure_io.write_canonical_json_at(root_fd, "zagrosi_implement_config.json", config)
        if state_pending:
            state = _detached_state.detached_state_default(config)
            _secure_io.write_canonical_json_at(root_fd, "zagrosi_implement_state.json", state)
        else:
            state = _detached_state.load_detached_state(root_fd, config)
            state_final = True
        if state_pending is False and config_pending:
            raise _models.DetachedImplementationError(
                "detached-setup-prefix-conflict",
                "A pending config cannot authorise an already-final state.",
            )
        if state_pending and not progress_pending:
            raise _models.DetachedImplementationError(
                "detached-setup-prefix-conflict",
                "A pending state requires the exact pending progress prefix.",
            )
        if progress_pending:
            progress_state = _detached_state.detached_progress_default(config)
            _secure_io.write_canonical_json_at(root_fd, "forge-progress.json", progress_state)
        else:
            progress_state = _detached_state.load_detached_progress(root_fd, config)
            progress_final = True
        if config_final and state_final and progress_final:
            pass

        _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
        _recovery.recover_section_record_transaction_locked(
            planning_dir,
            implementation_root,
            root_fd,
            config,
            guard,
            progress,
            require_lock_authority,
        )
        _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
        require_lock_authority()

        dependencies = _sections.dependency_graph(planning_dir, progress)
        known = set(progress["sections"])
        unknown_dependencies = {
            section: [dependency for dependency in dependencies.get(section, []) if dependency not in known]
            for section in progress["sections"]
            if any(dependency not in known for dependency in dependencies.get(section, []))
        }
        if unknown_dependencies:
            raise _models.DetachedImplementationError(
                "unknown-predecessors",
                "Section dependency graph contains predecessors absent from the manifest.",
                unknown_predecessors=unknown_dependencies,
            )
        completed_records = _pinners.detached_completed_records(root_fd, config, progress)
        completed = set(completed_records)
        ready = _sections.ready_sections(progress, dependencies, completed)
        remaining = [section for section in progress["sections"] if section not in completed]
        blocked = {
            section: [dependency for dependency in dependencies.get(section, []) if dependency not in completed]
            for section in remaining
            if section not in ready
        }
        guard.verify_unchanged()

        repo = _storage.git_info(target_dir)
        warnings: list[str] = []
        if repo.get("is_protected_branch"):
            warnings.append(f"Current git branch is protected-looking: {repo.get('branch')}")
        if repo.get("available") and not repo.get("working_tree_clean"):
            warnings.append(f"Working tree has {len(repo.get('dirty_files', []))} uncommitted change(s)")
        payload = {
            "success": bool(ready) or not remaining,
            "mode": "detached-frozen",
            "sections_dir": str(sections_dir),
            "target_dir": str(target_dir),
            "implementation_root": str(implementation_root),
            "state_dir": str(implementation_root),
            "config_path": str(implementation_root / "zagrosi_implement_config.json"),
            "state_path": str(implementation_root / "zagrosi_implement_state.json"),
            "reviews_dir": str(implementation_root / "code_review"),
            "evidence_dir": str(implementation_root / "evidence"),
            "pinners_dir": str(implementation_root / "pinners"),
            "planning_tree_sha256": guard.digest,
            "planning_file_count": guard.file_count,
            "planning_total_bytes": guard.total_bytes,
            "admission_pinner_path": str(admission_path),
            "admission_pinner_sha256": admission_sha256,
            "admission_state_sha256": admission_state_sha256,
            "detached_implementation_root_identity_digest": root_identity_digest,
            "target_root_identity_digest": setup_target_identity_digest,
            "implementation_sources": source_records,
            "section_progress": progress,
            "completed_sections": sorted(completed),
            "next_section": ready[0] if ready else None,
            "ready_sections": ready,
            "remaining_sections": remaining,
            "blocked_sections": blocked,
            "git": repo,
            "warnings": warnings,
        }
        if _gates.effective_flight_mode(args) != "off":
            preflight = _flights.implement_preflight_report(sections_dir, target_dir, args)
            payload["preflight"] = preflight
            payload["success"] = bool(payload["success"] and preflight.get("success"))
        _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
        require_lock_authority()
        return _output.print_json(payload, 0 if payload["success"] else 1)
    except _models.DetachedImplementationError as exc:
        return _output.print_json(
            _models.detached_error_payload(
                exc,
                mode="detached-frozen",
                planning_dir=str(planning_dir),
                implementation_root=str(implementation_root or _storage.absolute_path_no_follow(args.implementation_root)),
            ),
            1,
        )
    except OSError as exc:
        return _output.print_json(
            _models.detached_io_error_payload(
                exc,
                mode="detached-frozen",
                planning_dir=str(planning_dir),
                implementation_root=str(implementation_root or _storage.absolute_path_no_follow(args.implementation_root)),
            ),
            1,
        )
    finally:
        if lock_context is not None:
            lock_context.__exit__(*sys.exc_info())
        if guard is not None:
            guard.close()
        if root_fd is not None:
            os.close(root_fd)
        if target_fd is not None:
            os.close(target_fd)
