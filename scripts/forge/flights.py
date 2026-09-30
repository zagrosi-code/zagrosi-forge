"""Forge flights."""

from __future__ import annotations

from pathlib import Path
from contextlib import nullcontext
from typing import Any
import argparse
import os

from . import artifacts as _artifacts
from . import gates as _gates
from . import markdown as _markdown
from . import models as _models
from . import output as _output
from . import policy as _policy
from . import projects as _projects
from . import quality as _quality
from . import scoring as _scoring
from . import session as _session
from . import state as _state
from . import storage as _storage
from . import traceability as _traceability
from . import validation as _validation

def project_preflight_report(project_input: _models.ProjectInput, args: argparse.Namespace) -> dict[str, Any]:
    mode = _gates.effective_flight_mode(args)
    if mode == "off":
        return _gates.flight_payload(phase="project", stage="preflight", mode=mode, gates=[])
    plugin_root = _storage.resolve_path(getattr(args, "plugin_root", None)) if getattr(args, "plugin_root", None) else _storage.current_plugin_root()
    planning_dir = project_input.planning_dir
    input_file = project_input.input_file

    if project_input.input_mode == "chat":
        ok = project_input.brief_word_count > 0 and (
            input_file is None or (input_file.exists() and bool(_storage.read_text(input_file).strip()))
        )
        input_payload = {
            "mode": "chat",
            "planning_dir": str(planning_dir),
            "materialized_file": str(input_file) if input_file else None,
            "brief_word_count": project_input.brief_word_count,
            "generated_file": project_input.generated_file,
            "error": None if ok else "chat brief is empty or could not be materialized",
        }
        input_gate = _gates.direct_gate("chat-brief", ok, input_payload)
    else:
        ok, error = _projects.ensure_markdown_file(input_file, "requirements file") if input_file else (False, "requirements file missing")
        input_gate = _gates.direct_gate(
            "requirements-file",
            ok,
            {"path": str(input_file) if input_file else None, "error": error if error else None},
        )

    gates = [
        input_gate,
        *_gates.run_internal_gate_batch(
            [
                ("doctor", _gates.append_strict(["doctor", "--plugin-root", str(plugin_root)], mode), True),
                ("status", ["status", "--path", str(planning_dir)], False),
            ]
        ),
    ]
    return _gates.flight_payload(
        phase="project",
        stage="preflight",
        mode=mode,
        gates=gates,
        extras={
            "planning_dir": str(planning_dir),
            "plugin_root": str(plugin_root),
            "input_mode": project_input.input_mode,
            "input_file": str(input_file) if input_file else None,
        },
    )


def project_postflight_report(planning_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    mode = _gates.effective_flight_mode(args)
    if mode == "off":
        return _gates.flight_payload(phase="project", stage="postflight", mode=mode, gates=[])
    jobs: list[tuple[str, list[str], bool]] = []
    if _artifacts.interview_artifact(planning_dir, "project"):
        jobs.append(("lint-interview", _gates.append_strict(["lint-interview", "--phase", "project", "--planning-dir", str(planning_dir)], mode), True))
    jobs.extend(
        [
            ("lint-project-manifest", ["lint-project-manifest", "--planning-dir", str(planning_dir), "--strict"], True),
            ("status", ["status", "--path", str(planning_dir)], False),
        ]
    )
    gates = _gates.run_internal_gate_batch(jobs)
    return _gates.flight_payload(phase="project", stage="postflight", mode=mode, gates=gates, extras={"planning_dir": str(planning_dir)})


def plan_preflight_report(spec_file: Path, args: argparse.Namespace) -> dict[str, Any]:
    mode = _gates.effective_flight_mode(args)
    if mode == "off":
        return _gates.flight_payload(phase="plan", stage="preflight", mode=mode, gates=[])
    plugin_root = _storage.resolve_path(getattr(args, "plugin_root", None)) if getattr(args, "plugin_root", None) else _storage.current_plugin_root()
    planning_dir = spec_file.parent
    target_dir = _storage.resolve_path(getattr(args, "target_dir", None)) if getattr(args, "target_dir", None) else Path.cwd()
    ok, error = _projects.ensure_markdown_file(spec_file, "spec file")
    evidence_command = ["codebase-evidence", "--target-dir", str(target_dir), "--planning-dir", str(planning_dir)]
    if getattr(args, "write_evidence", False):
        evidence_command.append("--write")
    depth = _artifacts.planning_depth(planning_dir, getattr(args, "depth", _policy.DEFAULT_DEPTH) or _policy.DEFAULT_DEPTH)
    jobs: list[tuple[str, list[str], bool]] = [
        ("doctor", _gates.append_strict(["doctor", "--plugin-root", str(plugin_root)], mode), True),
    ]
    if getattr(args, "write_evidence", False) or not _markdown.is_lean_depth(depth):
        jobs.insert(1, ("codebase-evidence", evidence_command, False))
    gates = [
        _gates.direct_gate("spec-file", ok, {"path": str(spec_file), "error": error if error else None}),
        *_gates.run_internal_gate_batch(jobs),
    ]
    return _gates.flight_payload(
        phase="plan",
        stage="preflight",
        mode=mode,
        gates=gates,
        extras={"planning_dir": str(planning_dir), "plugin_root": str(plugin_root), "target_dir": str(target_dir)},
    )


def _plan_gate_jobs(planning_dir: Path, args: argparse.Namespace) -> list[tuple[str, list[str], bool]]:
    mode = _gates.effective_flight_mode(args)
    depth = _artifacts.planning_depth(planning_dir, getattr(args, "depth", _policy.DEFAULT_DEPTH) or _policy.DEFAULT_DEPTH)
    profile = getattr(args, "profile", "solo")
    compact = _markdown.is_lean_depth(depth)
    jobs: list[tuple[str, list[str], bool]] = []
    if _artifacts.interview_artifact(planning_dir, "plan"):
        jobs.append(("lint-interview", _gates.append_strict(["lint-interview", "--phase", "plan", "--planning-dir", str(planning_dir), "--profile", profile], mode), True))
    jobs.append(("lint-plan", _gates.append_strict(["lint-plan", "--planning-dir", str(planning_dir), "--depth", depth, "--profile", profile], mode), True))
    jobs.append(("lint-plan-artifacts", _gates.append_strict(["lint-plan-artifacts", "--planning-dir", str(planning_dir), "--profile", profile], mode), True))
    if not compact:
        jobs.extend(
            [
                ("lint-evidence", _gates.append_strict(["lint-evidence", "--planning-dir", str(planning_dir), "--profile", profile], mode), True),
                ("lint-artifact-schema", _gates.append_strict(["lint-artifact-schema", "--planning-dir", str(planning_dir), "--profile", profile], mode), True),
            ]
        )
    if (planning_dir / "sections" / "index.md").exists():
        jobs.extend(
            [
                ("lint-sections", _gates.append_strict(["lint-sections", "--planning-dir", str(planning_dir), "--depth", depth, "--profile", profile], mode), True),
                ("traceability", _gates.append_strict(["traceability", "--planning-dir", str(planning_dir), "--profile", profile], mode), True),
                ("lint-implementation-readiness", _gates.append_strict(["lint-implementation-readiness", "--planning-dir", str(planning_dir), "--profile", profile], mode), True),
            ]
        )
        if not compact:
            jobs.append(("forge-score", _gates.append_strict(["forge-score", "--planning-dir", str(planning_dir), "--depth", depth, "--profile", profile], mode), True))
        if getattr(args, "write_report", False):
            jobs.append(("report", ["report", "--planning-dir", str(planning_dir), "--depth", depth, "--profile", profile], False))
    return jobs


def _run_plan_gates(planning_dir: Path, args: argparse.Namespace, jobs) -> list[dict[str, Any]]:
    depth = _artifacts.planning_depth(planning_dir, getattr(args, "depth", None) or _policy.DEFAULT_DEPTH)
    context = _session._CLI_CONTEXT.get()
    previous_inputs = context.get("score_inputs") if context is not None else None
    reuse = (
        not _markdown.is_lean_depth(depth) and not getattr(args, "write_report", False)
        and context is not None and context["texts"] is not None
        and _gates.local_gate_available("forge-score", ["forge-score", "--planning-dir", str(planning_dir)])
    )
    if reuse:
        context["score_inputs"] = _scoring.FlightScoreInputs(planning_dir, depth, context["texts"])
    try:
        gates = _gates.run_internal_gate_batch(jobs)
    finally:
        if reuse:
            context["score_inputs"] = previous_inputs
    return gates


def plan_postflight_report(planning_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    mode = _gates.effective_flight_mode(args)
    gates = [] if mode == "off" else _run_plan_gates(planning_dir, args, _plan_gate_jobs(planning_dir, args))
    return _gates.flight_payload(phase="plan", stage="postflight", mode=mode, gates=gates, extras={"planning_dir": str(planning_dir),
                "depth_mode": _artifacts.planning_depth(planning_dir, getattr(args, "depth", None) or _policy.DEFAULT_DEPTH)})


def plan_admission_report(planning_dir: Path, *, depth: str | None = None, profile: str = "solo") -> dict[str, Any]:
    """Reuse complete selected-depth admission while all observed inputs stay current."""
    depth = _artifacts.planning_depth(planning_dir, depth or _policy.DEFAULT_DEPTH)
    args = argparse.Namespace(depth=depth, profile=profile, strict=True, flight_mode="strict", write_report=False)

    def analyze(observe):
        for path in (planning_dir, planning_dir / "sections", planning_dir / "reviews", planning_dir / "implementation"):
            observe(path)
        return {**plan_postflight_report(planning_dir, args), "depth_mode": depth}

    context = _session._CLI_CONTEXT.get()
    with nullcontext() if context and context.get("local_gates") and context.get("texts") is not None else _session.read_phase():
        # A child worker cannot contribute file observations to its parent cache.
        if not _gates.local_gate_available("lint-plan", ["lint-plan", "--planning-dir", str(planning_dir)]):
            return analyze(lambda path: path)
        return _session.cached_analysis("plan_admission", (planning_dir, depth, profile), analyze)


def implement_preflight_report(
    sections_dir: Path,
    target_dir: Path,
    args: argparse.Namespace,
    *,
    progress: dict[str, Any] | None = None,
    artifact_payload: dict[str, Any] | None = None,
    admission_payload: dict[str, Any] | None = None,
    repo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mode = _gates.effective_flight_mode(args)
    if mode == "off":
        return _gates.flight_payload(phase="implement", stage="preflight", mode=mode, gates=[])
    planning_dir = sections_dir.parent
    depth = _artifacts.planning_depth(planning_dir, getattr(args, "depth", _policy.DEFAULT_DEPTH) or _policy.DEFAULT_DEPTH)
    profile = getattr(args, "profile", "solo")
    strict = mode == "strict"
    repo = repo or (_storage.git_info(target_dir) if target_dir.exists() else {"available": False, "root": None})
    gates = [
        _gates.direct_gate("sections-directory", sections_dir.exists() and sections_dir.is_dir(), {"sections_dir": str(sections_dir)}),
        _gates.direct_gate("target-directory", target_dir.exists() and target_dir.is_dir(), {"target_dir": str(target_dir)}),
    ]
    if not getattr(args, "implementation_root", None):
        admission = admission_payload or plan_admission_report(planning_dir, depth=depth, profile=profile)
        gates.extend(admission["gates"])
    else:
        # Detached v1 admission retains its pinned artifact/readiness contract.
        artifact_payload = artifact_payload or _validation.plan_artifacts_payload(
            planning_dir, argparse.Namespace(profile=profile, strict=True),
        )
        gates.append(_gates.direct_gate("lint-plan-artifacts", bool(artifact_payload.get("success")), artifact_payload))
        for name, analyze in (
            ("sections", lambda: _scoring.section_findings_for_score(planning_dir, depth)),
            ("traceability", lambda: _traceability.traceability_analysis(planning_dir)),
            ("implementation-readiness", lambda: _scoring.implementation_readiness_analysis(planning_dir, 8)),
        ):
            findings, extras = analyze()
            payload = _quality.quality_payload(name, findings, extras, profile, strict)
            gates.append(_gates.direct_gate(name if name == "traceability" else f"lint-{name}", payload["success"], payload))
    warnings: list[str] = []
    if repo.get("is_protected_branch"):
        warnings.append(f"Current branch is protected-looking: {repo.get('branch')}")
    if repo.get("available") and not repo.get("working_tree_clean"):
        warnings.append(f"Working tree has {len(repo.get('dirty_files', []))} uncommitted change(s)")
    dirty_files = list(repo.get("dirty_files", []))
    git_payload = {
        key: repo.get(key)
        for key in ("available", "root", "branch", "is_protected_branch", "working_tree_clean")
    }
    git_payload["dirty_file_count"] = len(dirty_files)
    if dirty_files:
        git_payload["dirty_files"] = dirty_files[:12]
    if len(dirty_files) > 12:
        git_payload["dirty_files_truncated"] = True
    return _gates.flight_payload(
        phase="implement",
        stage="preflight",
        mode=mode,
        gates=gates,
        extras={"planning_dir": str(planning_dir), "target_dir": str(target_dir), "git": git_payload, "warnings": warnings},
    )


def implement_postflight_report(
    planning_dir: Path, args: argparse.Namespace, *, candidate_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _session.read_phase():
        return _implement_postflight_report(planning_dir, args, candidate_state=candidate_state)


def _implement_postflight_report(
    planning_dir: Path, args: argparse.Namespace, *, candidate_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from . import mutable_inputs, verification

    mode = _gates.effective_flight_mode(args)
    if mode == "off":
        return _gates.flight_payload(phase="implement", stage="postflight", mode=mode, gates=[])
    depth = _artifacts.planning_depth(planning_dir, getattr(args, "depth", _policy.DEFAULT_DEPTH) or _policy.DEFAULT_DEPTH)
    profile = getattr(args, "profile", "solo")
    target_dir = mutable_inputs.target_directory(planning_dir, getattr(args, "target_dir", None))
    recording_status = _state.implementation_recording_status(planning_dir, candidate_state)
    final_state_gates = recording_status["sections_recorded_complete"]
    plan_args = argparse.Namespace(depth=depth, profile=profile, strict=True, flight_mode="strict", write_report=False)
    jobs = _plan_gate_jobs(planning_dir, plan_args)
    if getattr(args, "diff_file", None) or getattr(args, "staged", False):
        command = ["implementation-drift", "--planning-dir", str(planning_dir), "--repo", str(target_dir), "--profile", profile]
        if getattr(args, "diff_file", None):
            command.extend(["--diff-file", str(_storage.resolve_path(args.diff_file))])
        if getattr(args, "staged", False):
            command.append("--staged")
        jobs.append(("implementation-drift", _gates.append_strict(command, mode), True))
    if getattr(args, "section_file", None):
        command = ["patch-scope", "--section-file", str(_storage.resolve_path(args.section_file)), "--repo", str(target_dir), "--profile", profile]
        if getattr(args, "diff_file", None):
            command.extend(["--diff-file", str(_storage.resolve_path(args.diff_file))])
        if getattr(args, "staged", False):
            command.append("--staged")
        jobs.append(("patch-scope", _gates.append_strict(command, mode), True))
    progress_state = recording_status["section_progress"].get("state")
    progress_gate: dict[str, Any] | None = None
    if progress_state in {"invalid_index", "no_index"}:
        progress_gate = _gates.direct_gate(
            "sections-index",
            False,
            {
                "section_progress": recording_status["section_progress"],
                "message": "Implementation postflight requires a valid sections/index.md.",
            },
        )
    elif final_state_gates or (candidate_state is None and recording_status["invalid_completed_sections"]):
        findings, extras = _state.implementation_state_analysis(planning_dir, candidate_state)
        state_payload = _quality.quality_payload("implementation-state", findings, extras, profile, mode == "strict")
        progress_gate = _gates.direct_gate("lint-implementation-state", state_payload["success"], state_payload)
    else:
        progress_gate = _gates.direct_gate(
            "implementation-progress",
            True,
            {
                "recording_state": recording_status["recording_state"],
                "recorded_sections": recording_status["recorded_sections"],
                "remaining_sections": recording_status["remaining_sections"],
                "deferred_gate": "lint-implementation-state",
                "message": "Implementation state lint is deferred until all sections are recorded complete.",
            },
            required=False,
        )
    if getattr(args, "write_report", False):
        jobs.append(("report", ["report", "--planning-dir", str(planning_dir), "--depth", depth, "--profile", profile], False))
    gates = _run_plan_gates(planning_dir, plan_args, jobs)
    if progress_gate is not None:
        gates.append(progress_gate)
    integration = None
    if candidate_state is None and final_state_gates:
        integration = verification.integration_report(planning_dir, target_dir)
        gates.append(_gates.direct_gate("integration-verification", integration["success"], integration))
    if candidate_state is None and recording_status["pending_sections"]:
        gates.append(_gates.direct_gate("pending-completion", False, {
            "pending_sections": recording_status["pending_sections"],
            "message": "Retry completion recording to resolve pending or failed postflight checks.",
        }))
    return _gates.flight_payload(
        phase="implement",
        stage="postflight",
        mode=mode,
        gates=gates,
        extras={"planning_dir": str(planning_dir), "target_dir": str(target_dir), **recording_status,
                "integration_verification": integration},
    )


def release_preflight_report(plugin_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    mode = _gates.effective_flight_mode(args)
    if mode == "off":
        return _gates.flight_payload(phase="release", stage="preflight", mode=mode, gates=[])
    gates = _gates.run_internal_gate_batch(
        [
            ("doctor", _gates.append_strict(["doctor", "--plugin-root", str(plugin_root)], mode), True),
            ("eval-suite", ["eval-suite", "--examples-dir", str(plugin_root / "examples")], False),
        ]
    )
    return _gates.flight_payload(phase="release", stage="preflight", mode=mode, gates=gates, extras={"plugin_root": str(plugin_root)})


def release_postflight_report(plugin_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    mode = _gates.effective_flight_mode(args)
    if mode == "off":
        return _gates.flight_payload(phase="release", stage="postflight", mode=mode, gates=[])
    command = ["release-check", "--plugin-root", str(plugin_root)]
    if getattr(args, "run_tests", False):
        command.append("--run-tests")
    gates = [_gates.run_internal_gate("release-check", command, timeout_seconds=600)]
    return _gates.flight_payload(phase="release", stage="postflight", mode=mode, gates=gates, extras={"plugin_root": str(plugin_root)})


def preflight(args: argparse.Namespace) -> int:
    if args.phase == "project":
        project_input, error = _projects.resolve_project_input(args, materialize_chat=False)
        if error:
            return _output.print_json({"success": False, "error": error}, 1)
        payload = project_preflight_report(project_input, args)
    elif args.phase == "plan":
        if not args.file:
            return _output.print_json({"success": False, "error": "--file is required for plan preflight"}, 1)
        payload = plan_preflight_report(_storage.resolve_path(args.file), args)
    elif args.phase == "implement":
        if not args.sections_dir:
            return _output.print_json({"success": False, "error": "--sections-dir is required for implement preflight"}, 1)
        payload = implement_preflight_report(_storage.resolve_path(args.sections_dir), _storage.resolve_path(args.target_dir or os.getcwd()), args)
    else:
        payload = release_preflight_report(_storage.resolve_path(args.plugin_root or _storage.current_plugin_root()), args)
    return _output.print_json(payload, 0 if payload["success"] else 1)


def postflight(args: argparse.Namespace) -> int:
    if args.phase == "project":
        if not args.planning_dir:
            return _output.print_json({"success": False, "error": "--planning-dir is required for project postflight"}, 1)
        payload = project_postflight_report(_storage.resolve_path(args.planning_dir), args)
    elif args.phase == "plan":
        if not args.planning_dir:
            return _output.print_json({"success": False, "error": "--planning-dir is required for plan postflight"}, 1)
        payload = plan_postflight_report(_storage.resolve_path(args.planning_dir), args)
    elif args.phase == "implement":
        planning_dir = _storage.resolve_path(args.planning_dir) if args.planning_dir else (_storage.resolve_path(args.sections_dir).parent if args.sections_dir else None)
        if planning_dir is None:
            return _output.print_json({"success": False, "error": "--planning-dir or --sections-dir is required for implement postflight"}, 1)
        payload = implement_postflight_report(planning_dir, args)
    else:
        payload = release_postflight_report(_storage.resolve_path(args.plugin_root or _storage.current_plugin_root()), args)
    return _output.print_json(payload, 0 if payload["success"] else 1)
