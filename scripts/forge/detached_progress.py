"""Forge detached progress."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse

from . import detached_authority as _detached_authority
from . import detached_context as _detached_context
from . import detached_contract as _detached_contract
from . import detached_state as _detached_state
from . import handoff_wire as _handoff_wire
from . import models as _models
from . import output as _output
from . import pinners as _pinners
from . import sections as _sections
from . import secure_io as _secure_io
from . import sources as _sources
from . import storage as _storage

def detached_next_section(args: argparse.Namespace) -> int:
    planning_dir = _storage.absolute_path_no_follow(args.planning_dir)
    implementation_root: Path | None = None
    try:
        with _detached_context.open_detached_context(
            planning_dir,
            args.implementation_root,
        ) as (implementation_root, root_fd, config, guard, require_lock_authority):
            progress = _sections.check_section_progress(planning_dir)
            if progress["state"] in {"invalid_index", "no_index"}:
                raise _models.DetachedImplementationError(
                    "invalid-sections-index",
                    "Cannot select a detached next section from an invalid sections index.",
                    section_progress=progress,
                )
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
            _sources.verify_implementation_sources(config)
            _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
            require_lock_authority()
            return _output.print_json(
                {
                    "success": bool(ready) or not remaining,
                    "mode": "detached-frozen",
                    "planning_dir": str(planning_dir),
                    "implementation_root": str(implementation_root),
                    "planning_tree_sha256": guard.digest,
                    "admission_pinner_sha256": config["admission_pinner_sha256"],
                    "next_section": ready[0] if ready else None,
                    "ready_sections": ready,
                    "remaining_sections": remaining,
                    "blocked_sections": blocked,
                    "completed_sections": sorted(completed),
                },
                0 if ready or not remaining else 1,
            )
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


def detached_implement_progress(args: argparse.Namespace) -> int:
    planning_dir = _storage.absolute_path_no_follow(args.planning_dir)
    implementation_root: Path | None = None
    try:
        with _detached_context.open_detached_context(
            planning_dir,
            args.implementation_root,
        ) as (implementation_root, root_fd, config, guard, require_lock_authority):
            progress = _sections.check_section_progress(planning_dir)
            if progress["state"] in {"invalid_index", "no_index"}:
                raise _models.DetachedImplementationError(
                    "invalid-sections-index",
                    "Cannot record detached progress against an invalid sections index.",
                    section_progress=progress,
                )
            section = args.section
            if section not in set(progress["sections"]):
                raise _models.DetachedImplementationError(
                    "unknown-section",
                    f"Section is absent from SECTION_MANIFEST: {section}",
                    section=section,
                )
            dependencies = _sections.dependency_graph(planning_dir, progress)
            completed_records = _pinners.detached_completed_records(root_fd, config, progress)
            incomplete_predecessors = [dependency for dependency in dependencies.get(section, []) if dependency not in completed_records]
            if section not in completed_records and incomplete_predecessors:
                raise _models.DetachedImplementationError(
                    "incomplete-predecessors",
                    f"Progress cannot start before every predecessor pinner closes: {section}",
                    section=section,
                    incomplete_predecessors=incomplete_predecessors,
                )
            event = {
                "timestamp": _storage.now_iso(),
                "section": section,
                "stage": args.stage,
                "command": args.command,
                "result": args.result,
                "notes": args.notes,
            }

            def append_event(state: dict[str, Any]) -> None:
                _handoff_wire.require_exact_fields(state, _detached_contract.DETACHED_PROGRESS_FIELDS, "Detached implementation progress")
                if (
                    state.get("schema") != _detached_contract.DETACHED_PROGRESS_SCHEMA
                    or state.get("mode") != "detached-frozen"
                    or state.get("planning_tree_sha256") != config.get("planning_tree_sha256")
                    or state.get("admission_pinner_sha256") != config.get("admission_pinner_sha256")
                    or not isinstance(state.get("events"), list)
                ):
                    raise _models.DetachedImplementationError(
                        "invalid-detached-progress",
                        "Detached progress is not bound to the current planning tree and admission pinner.",
                    )
                state["events"].append(event)

            guard.verify_unchanged()
            _sources.verify_implementation_sources(config)
            state = _detached_state.load_detached_progress(root_fd, config)
            append_event(state)
            _secure_io.write_canonical_json_at(root_fd, "forge-progress.json", state)
            guard.verify_unchanged()
            _sources.verify_implementation_sources(config)
            _detached_authority.verify_detached_authorities(planning_dir, implementation_root, root_fd, config, guard)
            require_lock_authority()
            return _output.print_json(
                {
                    "success": True,
                    "mode": "detached-frozen",
                    "planning_dir": str(planning_dir),
                    "implementation_root": str(implementation_root),
                    "planning_tree_sha256": guard.digest,
                    "admission_pinner_sha256": config["admission_pinner_sha256"],
                    "state_path": str(implementation_root / "forge-progress.json"),
                    "event": event,
                    "event_count": len(state["events"]),
                }
            )
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
