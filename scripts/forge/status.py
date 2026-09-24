"""Forge status."""

from __future__ import annotations

from typing import Any
from pathlib import Path
import argparse
import shlex
import sys

from . import artifacts as _artifacts
from . import planning_contract as _planning_contract
from . import output as _output
from . import resume as _resume
from . import sections as _sections
from . import state as _state
from . import storage as _storage


def detached_status(path: Path, implementation_root: Path) -> int:
    """Locate the next authority check without performing recovery."""
    from . import detached_authority as _detached_authority
    from . import models as _models

    try:
        planning_dir = _detached_authority.recover_planning_dir_from_detached_root(str(implementation_root))
    except _models.DetachedImplementationError as exc:
        return _output.print_json(_models.detached_error_payload(exc, path=str(path)), 1)
    except OSError as exc:
        return _output.print_json(_models.detached_io_error_payload(exc, path=str(path)), 1)
    command = shlex.join([
        sys.executable, str(_storage.current_plugin_root() / "scripts/zagrosi_skills.py"),
        "next-section", "--planning-dir", str(planning_dir),
        "--implementation-root", str(implementation_root),
    ])
    return _output.print_json({
        "success": True,
        "mode": "detached-frozen",
        "path": str(path),
        "planning_dir": str(planning_dir),
        "implementation_root": str(implementation_root),
        "readiness_verified": False,
        "next_action": "reopen detached authorities and select the next section",
        "next_command": command,
    })

def status(args: argparse.Namespace) -> int:
    raw_path = _storage.absolute_path_no_follow(args.path)
    candidate = raw_path.parent if raw_path.name in {"zagrosi_implement_config.json", "zagrosi_implement_state.json", "forge-progress.json"} else raw_path
    mutable_root = candidate.parent if candidate.name == "implementation" and (candidate.parent / "sections/index.md").is_file() else None
    detached_config = candidate / "zagrosi_implement_config.json"
    if mutable_root is None and (detached_config.exists() or detached_config.is_symlink()):
        return detached_status(raw_path, candidate)
    path = _storage.resolve_path(args.path)
    if mutable_root is not None:
        planning_dir = _storage.resolve_path(str(mutable_root))
    elif path.is_file():
        planning_dir = path.parent.parent if path.parent.name == "sections" else path.parent
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
    implementation_state = _state.implementation_state_path(planning_dir)
    files = {
        "project_manifest": str(planning_dir / "project-manifest.md") if (planning_dir / "project-manifest.md").exists() else None,
        "zagrosi_project_state": str(project_state) if project_state.exists() else None,
        "zagrosi_plan_config": str(plan_config) if plan_config.exists() else None,
        "implementation_state": str(implementation_state) if implementation_state.exists() else None,
    }
    files = {key: value for key, value in files.items() if value}
    has_plan = plan_config.exists() or section_progress["state"] != "no_index"
    plan_artifacts = _artifacts.plan_artifact_state(planning_dir) if has_plan else None
    plan_config_payload = _storage.load_json(plan_config) if plan_config.exists() else {}
    next_action = "start zagrosi-project or zagrosi-plan"
    details: dict[str, Any] = {}
    if has_plan:
        next_action = _artifacts.next_plan_action(plan_artifacts or {}, section_progress, plan_config_payload)
        if _planning_contract.scaffold_unfinished(planning_dir):
            details["scaffold_unfinished"] = True
            next_action = "complete the draft plan: choose section boundaries, fill the contract, and record review"
        elif section_progress["state"] == "complete":
            readiness = _state.mutable_admitted_readiness(planning_dir)
            details.update({key: value for key, value in readiness.items() if key != "success"})
            if not readiness["admission"]["success"]:
                next_action = "repair planning admission findings before implementation"
            else:
                pending = readiness["pending_sections"]
                if pending:
                    brief = _resume.resume_brief(planning_dir, pending[0])
                    details["resume"] = brief
                    next_action = brief["next_action"] if brief else "recheck pending completion state"
                elif readiness["next_section"] and implementation_state.exists():
                    section = readiness["next_section"]
                    brief = _resume.resume_brief(planning_dir, section)
                    details["resume"] = brief
                    next_action = brief["next_action"] if brief else f"implement {section}"
                elif readiness["remaining_sections"] and not readiness["ready_sections"]:
                    next_action = "resolve blocked section dependencies"
                elif not readiness["remaining_sections"]:
                    next_action = "final verification and summary"
    elif project_state.exists():
        next_action = "finish project manifest/spec generation"

    payload: dict[str, Any] = {
        "success": True,
        "path": str(path),
        "planning_dir": str(planning_dir),
        "files": files,
        "section_progress": section_progress,
        "next_action": next_action,
        **details,
    }
    if plan_artifacts is not None:
        payload["plan_artifacts"] = _artifacts.plan_artifact_payload(plan_artifacts)
    return _output.print_json(payload)
