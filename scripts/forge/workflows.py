"""Forge workflows."""

from __future__ import annotations

import argparse
import os

from . import artifacts as _artifacts
from . import flights as _flights
from . import gates as _gates
from . import markdown as _markdown
from . import output as _output
from . import policy as _policy
from . import projects as _projects
from . import sections as _sections
from . import state as _state
from . import storage as _storage
from . import traceability as _traceability
from . import validation as _validation

def deep_project_setup(args: argparse.Namespace) -> int:
    project_input, error = _projects.resolve_project_input(args)
    if error or project_input is None:
        return _output.print_json({"success": False, "error": error}, 1)

    input_file = project_input.input_file
    planning_dir = project_input.planning_dir
    state_dir = planning_dir / ".zagrosi-project"
    state_path = state_dir / "session.json"
    legacy_state_path = planning_dir / ".deep-project" / "session.json"
    if not state_path.exists() and legacy_state_path.exists():
        state_path = legacy_state_path
        state_dir = legacy_state_path.parent
    mode = "resume" if state_path.exists() else "new"

    if state_path.exists():
        state = _storage.load_json(state_path)
    else:
        state = {
            "initial_file": str(input_file) if input_file else None,
            "initial_source": project_input.input_mode,
            "created_at": _storage.now_iso(),
            "depth_mode": args.depth,
            "contract_version": _policy.PROJECT_CONTRACT_VERSION,
            "workflow": "zagrosi-project",
        }
        _storage.write_json(state_path, state)

    warnings: list[str] = list(project_input.warnings)
    if input_file and state.get("initial_file") and state.get("initial_file") != str(input_file):
        warnings.append(f"Session was created for {state.get('initial_file')}, now using {input_file}")
    if state.get("initial_source") and state.get("initial_source") != project_input.input_mode:
        warnings.append(f"Session was created from {state.get('initial_source')}, now using {project_input.input_mode}")

    manifest_path = planning_dir / "project-manifest.md"
    split_dirs = [p for p in planning_dir.iterdir() if p.is_dir() and _policy.SPLIT_RE.match(p.name)]
    specs = [p for p in split_dirs if (p / "spec.md").exists() and _storage.read_text(p / "spec.md").strip()]

    if split_dirs and len(specs) == len(split_dirs):
        resume_step = 7
        resume_label = "complete"
    elif split_dirs:
        resume_step = 6
        resume_label = "spec_generation"
    elif manifest_path.exists():
        resume_step = 4
        resume_label = "confirmation_or_directory_creation"
    else:
        resume_step = 2
        resume_label = "split_analysis"

    if _artifacts.interview_artifact(planning_dir, "project"):
        warnings.extend(_projects.interview_warning_messages(planning_dir, "project"))

    payload = {
        "success": True,
        "mode": mode,
        "planning_dir": str(planning_dir),
        "state_dir": str(state_dir),
        "initial_file": str(input_file) if input_file else None,
        "input_mode": project_input.input_mode,
        "generated_requirements_file": str(input_file) if project_input.generated_file and input_file else None,
        "brief_word_count": project_input.brief_word_count,
        "depth_mode": state.get("depth_mode", args.depth),
        "resume_step": resume_step,
        "resume_label": resume_label,
        "split_directories": [str(p) for p in sorted(split_dirs)],
        "specs_complete": [str(p / "spec.md") for p in sorted(specs)],
        "warnings": warnings,
    }
    if _gates.effective_flight_mode(args) != "off":
        payload["preflight"] = _flights.project_preflight_report(project_input, args)
    return _output.print_json(payload)


def deep_project_create_dirs(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.planning_dir)
    manifest_path = planning_dir / "project-manifest.md"
    if not manifest_path.exists():
        return _output.print_json({"success": False, "error": f"Missing manifest: {manifest_path}"}, 1)

    splits, errors = _markdown.parse_numbered_manifest(_storage.read_text(manifest_path), "SPLIT_MANIFEST", _policy.SPLIT_RE)
    if errors:
        return _output.print_json({"success": False, "errors": errors}, 1)

    created: list[str] = []
    existing: list[str] = []
    missing_specs: list[str] = []
    for split in splits:
        directory = planning_dir / split
        if directory.exists():
            existing.append(str(directory))
        else:
            directory.mkdir(parents=True)
            created.append(str(directory))
        spec_path = directory / "spec.md"
        if not spec_path.exists() or not _storage.read_text(spec_path).strip():
            missing_specs.append(str(spec_path))

    payload = {
        "success": True,
        "planning_dir": str(planning_dir),
        "splits": splits,
        "created": created,
        "existing": existing,
        "missing_specs": missing_specs,
    }
    if _gates.effective_flight_mode(args) != "off":
        payload["postflight"] = _flights.project_postflight_report(planning_dir, args)
    return _output.print_json(payload)


def deep_plan_setup(args: argparse.Namespace) -> int:
    spec_file = _storage.resolve_path(args.file)
    ok, error = _projects.ensure_markdown_file(spec_file, "spec file")
    if not ok:
        return _output.print_json({"success": False, "error": error}, 1)

    planning_dir = spec_file.parent
    config_path = planning_dir / "zagrosi_plan_config.json"
    legacy_config_path = planning_dir / "deep_plan_config.json"
    if not config_path.exists() and legacy_config_path.exists():
        config_path = legacy_config_path
    mode = "resume" if config_path.exists() else "new"
    if config_path.exists():
        config = _storage.load_json(config_path)
    else:
        config = {
            "initial_file": str(spec_file),
            "planning_dir": str(planning_dir),
            "plugin_root": str(_storage.resolve_path(args.plugin_root)) if args.plugin_root else None,
            "review_mode": args.review_mode,
            "depth_mode": args.depth,
            "workflow": "zagrosi-plan",
            "created_at": _storage.now_iso(),
        }
        _storage.write_json(config_path, config)

    artifacts = _artifacts.plan_artifact_state(planning_dir)
    files = {name: artifacts[name] for name in ("research", "interview", "spec", "plan", "integration_notes")}
    files["plan_tdd"] = artifacts["tdd"]
    reviews_dir = planning_dir / "reviews"
    reviews = sorted(str(p) for p in reviews_dir.glob("*.md")) if reviews_dir.exists() else []
    section_progress = _sections.check_section_progress(planning_dir)

    if section_progress["state"] == "complete":
        resume_step = None
        resume_label = "complete"
    elif section_progress["state"] in {"has_index", "partial"}:
        resume_step = 19
        resume_label = "write_sections"
    elif files["plan"] and (reviews or config.get("review_mode") == "skip"):
        resume_step = 18
        resume_label = "create_section_index"
    elif files["plan"]:
        resume_step = 13
        resume_label = "review_plan"
    else:
        resume_step = 11
        resume_label = "write_plan"

    warnings: list[str] = []
    if files["interview"]:
        warnings.extend(_projects.interview_warning_messages(planning_dir, "plan"))

    payload = {
        "success": True,
        "mode": mode,
        "planning_dir": str(planning_dir),
        "config_path": str(config_path),
        "initial_file": str(spec_file),
        "review_mode": config.get("review_mode", args.review_mode),
        "depth_mode": config.get("depth_mode", args.depth),
        "resume_step": resume_step,
        "resume_label": resume_label,
        "files_found": {k: str(v) for k, v in files.items() if v},
        "reviews": reviews,
        "section_progress": section_progress,
        "warnings": warnings,
    }
    if _gates.effective_flight_mode(args) != "off":
        payload["preflight"] = _flights.plan_preflight_report(spec_file, args)
    return _output.print_json(payload)


def deep_implement_setup(args: argparse.Namespace) -> int:
    if getattr(args, "implementation_root", None):
        from . import detached_setup as _detached_setup

        return _detached_setup.detached_implement_setup(args)
    sections_dir = _storage.resolve_path(args.sections_dir)
    target_dir = _storage.resolve_path(args.target_dir or os.getcwd())
    if not sections_dir.exists() or not sections_dir.is_dir():
        return _output.print_json({"success": False, "error": f"Sections directory not found: {sections_dir}"}, 1)
    if not target_dir.exists() or not target_dir.is_dir():
        return _output.print_json({"success": False, "error": f"Target directory not found: {target_dir}"}, 1)

    planning_dir = sections_dir.parent
    progress = _sections.check_section_progress(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"}:
        return _output.print_json({"success": False, "section_progress": progress}, 1)

    artifact_payload = _validation.plan_artifacts_payload(planning_dir, argparse.Namespace(profile=args.profile, strict=True))
    if not artifact_payload["success"]:
        artifact_payload["error"] = "Forge planning process is incomplete; finish zagrosi-plan before implementation."
        return _output.print_json(artifact_payload, 1)

    state_dir = planning_dir / "implementation"
    config_path = state_dir / "zagrosi_implement_config.json"
    state_path = state_dir / "zagrosi_implement_state.json"
    legacy_config_path = state_dir / "deep_implement_config.json"
    legacy_state_path = state_dir / "deep_implement_state.json"
    if not config_path.exists() and legacy_config_path.exists():
        config_path = legacy_config_path
    if not state_path.exists() and legacy_state_path.exists():
        state_path = legacy_state_path
    state_dir.mkdir(parents=True, exist_ok=True)

    if state_path.exists():
        state = _storage.load_json(state_path)
    else:
        state = {"completed_sections": {}, "created_at": _storage.now_iso()}
        _storage.write_json(state_path, state)

    config = {
        "sections_dir": str(sections_dir),
        "target_dir": str(target_dir),
        "planning_dir": str(planning_dir),
        "test_command": progress.get("project_config", {}).get("test_command"),
        "runtime": progress.get("project_config", {}).get("runtime"),
    }
    _storage.write_json(config_path, config)

    completed = _state.completed_sections(planning_dir, state)
    dependencies = _sections.dependency_graph(planning_dir, progress)
    readiness = _state.mutable_readiness_snapshot(progress, dependencies, completed)
    repo = _storage.git_info(target_dir)
    warnings: list[str] = []
    if repo.get("is_protected_branch"):
        warnings.append(f"Current git branch is protected-looking: {repo.get('branch')}")
    if repo.get("available") and not repo.get("working_tree_clean"):
        warnings.append(f"Working tree has {len(repo.get('dirty_files', []))} uncommitted change(s)")

    payload = {
        "success": True,
        "sections_dir": str(sections_dir),
        "target_dir": str(target_dir),
        "state_dir": str(state_dir),
        "config_path": str(config_path),
        "state_path": str(state_path),
        "section_progress": progress,
        **readiness,
        "warnings": warnings,
    }
    if _gates.effective_flight_mode(args) == "off":
        payload["git"] = repo
    else:
        preflight = _flights.implement_preflight_report(
            sections_dir,
            target_dir,
            args,
            progress=progress,
            artifact_payload=artifact_payload,
            repo=repo,
        )
        payload["preflight"] = preflight
        payload["success"] = bool(payload["success"] and preflight.get("success"))
    return _output.print_json(payload, 0 if payload["success"] else 1)


def deep_implement_record_section(args: argparse.Namespace) -> int:
    if getattr(args, "implementation_root", None):
        from . import detached_record as _detached_record

        return _detached_record.detached_implement_record_section(args)
    sections_dir = _storage.resolve_path(args.sections_dir)
    planning_dir = sections_dir.parent
    artifact_payload = _validation.plan_artifacts_payload(planning_dir, argparse.Namespace(profile=args.profile, strict=True))
    if not artifact_payload["success"]:
        artifact_payload["error"] = "Forge planning process is incomplete; finish zagrosi-plan before recording implementation."
        return _output.print_json(artifact_payload, 1)
    progress = _sections.check_section_progress(planning_dir)
    known = set(progress.get("sections", []))
    if args.section not in known:
        return _output.print_json(
            {
                "success": False,
                "error_code": "unknown-section",
                "error": f"Section is absent from SECTION_MANIFEST: {args.section}",
                "section": args.section,
            },
            1,
        )
    state_path = _state.implementation_state_path(planning_dir)
    state = _state.load_implementation_state(planning_dir)
    dependencies = _sections.dependency_graph(planning_dir, progress)
    unknown_predecessors = sorted(dependency for dependency in dependencies.get(args.section, []) if dependency not in known)
    if unknown_predecessors:
        return _output.print_json(
            {
                "success": False,
                "error_code": "unknown-predecessors",
                "error": f"Section names predecessors absent from SECTION_MANIFEST: {args.section}",
                "section": args.section,
                "unknown_predecessors": unknown_predecessors,
            },
            1,
        )
    completed = state.get("completed_sections", {})
    completed_names = _state.completed_sections(planning_dir, state)
    incomplete_predecessors = [dependency for dependency in dependencies.get(args.section, []) if dependency not in completed_names]
    if incomplete_predecessors:
        return _output.print_json(
            {
                "success": False,
                "error_code": "incomplete-predecessors",
                "error": f"Section cannot be recorded before every predecessor closes: {args.section}",
                "section": args.section,
                "incomplete_predecessors": incomplete_predecessors,
            },
            1,
        )
    compact = _markdown.is_lean_depth(_artifacts.planning_depth(planning_dir))
    verification = _markdown.normalize_repeated(args.verification)
    review_status = getattr(args, "review_status", None)
    section_record = {
        "completed_at": _storage.now_iso(),
        "commit": args.commit,
        "notes": args.notes,
        "files_changed": _markdown.normalize_repeated(args.files_changed),
        "test_files": _markdown.normalize_repeated(args.test_files),
        "review_artifacts": _markdown.normalize_repeated(args.review_artifacts),
        "review_status": review_status,
        "evidence_rows": _markdown.normalize_repeated(getattr(args, "evidence_rows", [])),
        "verification": verification,
        "commit_status": args.commit_status or ("recorded" if args.commit else "not_recorded"),
    }
    findings = _state.completion_evidence_findings(planning_dir, args.section, section_record)
    if findings:
        return _output.print_json({
            "success": False,
            "error_code": "incomplete-lean-record" if compact else "incomplete-completion-record",
            "error": "Completion requires a passing review and recorded verification evidence.",
            "section": args.section,
            "findings": [finding.to_dict() for finding in findings],
        }, 1)

    completed = dict(completed) if isinstance(completed, dict) else {}
    pending = dict(state.get("pending_sections", {}))
    candidate = {**state, "completed_sections": {**completed, args.section: section_record},
                 "pending_sections": {name: record for name, record in pending.items() if name != args.section}}
    mode = _gates.effective_flight_mode(args)
    if args.section in pending and mode == "off":
        return _output.print_json({
            "success": False,
            "error_code": "pending-completion",
            "error": "Retry pending completion with postflight enabled; its checks have not passed.",
            "section": args.section,
        }, 1)
    postflight = None
    if mode != "off" and (not compact or mode == "strict" or args.section in pending):
        # Publish evidence as pending first: interruptions and failed gates cannot unlock successors.
        state["completed_sections"] = completed
        state["pending_sections"] = {**pending, args.section: section_record}
        _storage.write_json(state_path, state)
        postflight = _flights.implement_postflight_report(planning_dir, args, candidate_state=candidate)
        if not postflight["success"]:
            state["pending_sections"][args.section] = {**section_record, "failed_postflight": postflight}
        else:
            state = candidate
    else:
        state = candidate
    _storage.write_json(state_path, state)
    traceability_path = None
    if not compact and not _artifacts.compact_plan_descriptor(planning_dir):
        traceability_path = _traceability.refresh_traceability_matrix(planning_dir)
    readiness = _state.mutable_readiness_snapshot(
        progress,
        dependencies,
        _state.completed_sections(planning_dir, state),
    )
    payload = {
        "success": postflight is None or postflight["success"],
        "state_path": str(state_path),
        "section": args.section,
        "record": section_record,
        "traceability_matrix": str(traceability_path) if traceability_path else None,
        **readiness,
    }
    if postflight is not None:
        payload["postflight"] = postflight
    return _output.print_json(payload, 0 if payload["success"] else 1)
