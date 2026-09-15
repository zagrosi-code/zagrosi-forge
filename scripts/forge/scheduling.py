"""Forge scheduling."""

from __future__ import annotations

from typing import Any
import argparse

from . import output as _output
from . import sections as _sections
from . import state as _state
from . import storage as _storage

def section_estimates(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"}:
        return _output.print_json({"success": False, "section_progress": progress}, 1)
    deps = _sections.dependency_graph(planning_dir, progress)
    estimates = [
        _sections.section_metrics(section, planning_dir / "sections" / f"{section}.md", deps)
        for section in progress["sections"]
        if (planning_dir / "sections" / f"{section}.md").exists()
    ]
    return _output.print_json(
        {
            "success": True,
            "planning_dir": str(planning_dir),
            "section_progress": progress,
            "estimates": estimates,
        }
    )


def next_section(args: argparse.Namespace) -> int:
    if getattr(args, "implementation_root", None):
        from . import detached_progress as _detached_progress

        return _detached_progress.detached_next_section(args)
    planning_dir = _storage.resolve_path(args.planning_dir)
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"}:
        return _output.print_json({"success": False, "section_progress": progress}, 1)
    deps = _sections.dependency_graph(planning_dir, progress)
    completed = _state.completed_sections(planning_dir)
    readiness = _state.mutable_readiness_snapshot(progress, deps, completed)
    remaining = readiness["remaining_sections"]
    ready = readiness["ready_sections"]
    return _output.print_json(
        {
            "success": bool(ready) or not remaining,
            "planning_dir": str(planning_dir),
            **readiness,
        },
        0 if ready or not remaining else 1,
    )


def parallel_plan(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"}:
        return _output.print_json({"success": False, "section_progress": progress}, 1)
    deps = _sections.dependency_graph(planning_dir, progress)
    completed = _state.completed_sections(planning_dir)
    known = set(progress["sections"])
    remaining = [section for section in progress["sections"] if section not in completed]
    available = set(completed)
    layers: list[list[str]] = []
    unresolved = set(remaining)

    while unresolved:
        layer = sorted(
            section
            for section in unresolved
            if all(dep in available for dep in deps.get(section, []))
        )
        if not layer:
            break
        layers.append(layer)
        available.update(layer)
        unresolved.difference_update(layer)

    unknown_dependencies = {
        section: [dep for dep in deps.get(section, []) if dep not in known]
        for section in progress["sections"]
        if any(dep not in known for dep in deps.get(section, []))
    }
    success = not unresolved and not unknown_dependencies
    return _output.print_json(
        {
            "success": success,
            "planning_dir": str(planning_dir),
            "completed_sections": sorted(completed),
            "layers": layers,
            "blocked_or_cyclic": sorted(unresolved),
            "unknown_dependencies": unknown_dependencies,
        },
        0 if success else 1,
    )


def implement_progress(args: argparse.Namespace) -> int:
    if getattr(args, "implementation_root", None):
        from . import detached_progress as _detached_progress

        return _detached_progress.detached_implement_progress(args)
    planning_dir = _storage.resolve_path(args.planning_dir)
    state_dir = planning_dir / "implementation"
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "forge-progress.json"
    event = {
        "timestamp": _storage.now_iso(),
        "section": args.section,
        "stage": args.stage,
        "command": args.command,
        "result": args.result,
        "notes": args.notes,
    }

    def default_state() -> dict[str, Any]:
        return {"events": [], "created_at": _storage.now_iso()}

    def append_event(state: dict[str, Any]) -> None:
        state.setdefault("events", []).append(event)

    try:
        state = _storage.update_json_locked(path, default_state, append_event)
    except TimeoutError as exc:
        return _output.print_json({"success": False, "planning_dir": str(planning_dir), "state_path": str(path), "error": str(exc)}, 1)
    return _output.print_json({"success": True, "planning_dir": str(planning_dir), "state_path": str(path), "event": event, "event_count": len(state["events"])})
