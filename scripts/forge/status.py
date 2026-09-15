"""Forge status."""

from __future__ import annotations

from typing import Any
import argparse

from . import artifacts as _artifacts
from . import output as _output
from . import sections as _sections
from . import storage as _storage

def status(args: argparse.Namespace) -> int:
    path = _storage.resolve_path(args.path)
    if path.is_file():
        planning_dir = path.parent
    elif path.name == "sections":
        planning_dir = path.parent
    else:
        planning_dir = path

    project_state = planning_dir / ".zagrosi-project" / "session.json"
    legacy_project_state = planning_dir / ".deep-project" / "session.json"
    if not project_state.exists() and legacy_project_state.exists():
        project_state = legacy_project_state
    plan_config = planning_dir / "zagrosi_plan_config.json"
    legacy_plan_config = planning_dir / "deep_plan_config.json"
    if not plan_config.exists() and legacy_plan_config.exists():
        plan_config = legacy_plan_config
    section_progress = _sections.check_section_progress(planning_dir)
    implementation_state = planning_dir / "implementation" / "zagrosi_implement_state.json"
    legacy_implementation_state = planning_dir / "implementation" / "deep_implement_state.json"
    if not implementation_state.exists() and legacy_implementation_state.exists():
        implementation_state = legacy_implementation_state
    files = {
        "project_manifest": str(planning_dir / "project-manifest.md") if (planning_dir / "project-manifest.md").exists() else None,
        "zagrosi_project_state": str(project_state) if project_state.exists() else None,
        "zagrosi_plan_config": str(plan_config) if plan_config.exists() else None,
        "implementation_state": str(implementation_state) if implementation_state.exists() else None,
    }
    files = {key: value for key, value in files.items() if value}
    plan_artifacts = _artifacts.plan_artifact_state(planning_dir) if plan_config.exists() else None
    plan_config_payload = _storage.load_json(plan_config) if plan_config.exists() else {}
    next_action = "start zagrosi-project or zagrosi-plan"
    if section_progress["state"] == "complete" and not implementation_state.exists():
        next_action = "run zagrosi-implement"
    elif implementation_state.exists():
        state = _storage.load_json(implementation_state)
        completed = set(state.get("completed_sections", {}))
        remaining = [section for section in section_progress.get("sections", []) if section not in completed]
        next_action = f"implement {remaining[0]}" if remaining else "final verification and summary"
    elif plan_config.exists():
        next_action = _artifacts.next_plan_action(plan_artifacts or {}, section_progress, plan_config_payload)
    elif project_state.exists():
        next_action = "finish project manifest/spec generation"

    payload: dict[str, Any] = {
        "success": True,
        "path": str(path),
        "planning_dir": str(planning_dir),
        "files": files,
        "section_progress": section_progress,
        "next_action": next_action,
    }
    if plan_artifacts is not None:
        payload["plan_artifacts"] = _artifacts.plan_artifact_payload(plan_artifacts)
    return _output.print_json(payload)
