"""Forge state."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse

from . import artifacts as _artifacts
from . import markdown as _markdown
from . import models as _models
from . import output as _output
from . import quality as _quality
from . import sections as _sections
from . import storage as _storage

def completed_sections(planning_dir: Path) -> set[str]:
    state_path = implementation_state_path(planning_dir)
    if not state_path.exists():
        return set()
    state = _storage.load_json(state_path)
    completed = state.get("completed_sections", {})
    return set(completed) if isinstance(completed, dict) else set()


def implementation_recording_status(planning_dir: Path) -> dict[str, Any]:
    progress = _sections.check_section_progress(planning_dir)
    sections = progress.get("sections", []) if progress.get("state") not in {"invalid_index", "no_index"} else []
    known_sections = set(sections)
    recorded = completed_sections(planning_dir)
    recorded_known = sorted(section for section in recorded if section in known_sections)
    remaining = [section for section in sections if section not in recorded]
    sections_recorded_complete = bool(sections) and not remaining and progress.get("state") == "complete"
    if sections_recorded_complete:
        recording_state = "complete"
    elif recorded_known:
        recording_state = "partial"
    else:
        recording_state = "not_started"
    return {
        "section_progress": progress,
        "recording_state": recording_state,
        "sections_recorded_complete": sections_recorded_complete,
        "recorded_sections": recorded_known,
        "remaining_sections": remaining,
        "unknown_recorded_sections": sorted(section for section in recorded if section not in known_sections),
    }


def implementation_state_path(planning_dir: Path) -> Path:
    state_path = planning_dir / "implementation" / "zagrosi_implement_state.json"
    legacy_state_path = planning_dir / "implementation" / "deep_implement_state.json"
    if not state_path.exists() and legacy_state_path.exists():
        return legacy_state_path
    return state_path


def load_implementation_state(planning_dir: Path) -> dict[str, Any]:
    state_path = implementation_state_path(planning_dir)
    return _storage.load_json(state_path) if state_path.exists() else {"completed_sections": {}, "created_at": _storage.now_iso()}


def mutable_readiness_snapshot(
    progress: dict[str, Any],
    dependencies: dict[str, list[str]],
    completed: set[str],
) -> dict[str, Any]:
    ready = _sections.ready_sections(progress, dependencies, completed)
    remaining = [section for section in progress["sections"] if section not in completed]
    blocked = {
        section: [dependency for dependency in dependencies.get(section, []) if dependency not in completed]
        for section in remaining
        if section not in ready
    }
    return {
        "next_section": ready[0] if ready else None,
        "ready_sections": ready,
        "remaining_sections": remaining,
        "blocked_sections": blocked,
        "completed_sections": sorted(completed),
    }


def implementation_evidence_by_section(planning_dir: Path) -> dict[str, dict[str, Any]]:
    state = load_implementation_state(planning_dir)
    completed = state.get("completed_sections", {})
    return completed if isinstance(completed, dict) else {}


def compact_section_evidence(record: dict[str, Any]) -> str:
    parts: list[str] = []
    commit = record.get("commit")
    if commit:
        parts.append(f"commit `{commit}`")
    commit_status = record.get("commit_status")
    if commit_status and not commit:
        parts.append(f"commit status `{commit_status}`")
    for key, label in (
        ("files_changed", "files"),
        ("test_files", "tests"),
        ("review_artifacts", "review"),
        ("verification", "verification"),
    ):
        values = _markdown.normalize_repeated(record.get(key, [])) if isinstance(record, dict) else []
        rendered = _output.compact_values(values, label)
        if rendered:
            parts.append(rendered)
    return "; ".join(parts) if parts else "-"


def lint_implementation_state(args: argparse.Namespace) -> int:
    sections_dir = _storage.resolve_path(args.sections_dir)
    planning_dir = sections_dir.parent
    findings: list[_models.Finding] = []
    progress = _sections.check_section_progress(planning_dir)
    state_path = planning_dir / "implementation" / "zagrosi_implement_state.json"
    legacy_state_path = planning_dir / "implementation" / "deep_implement_state.json"
    if not state_path.exists() and legacy_state_path.exists():
        state_path = legacy_state_path
    code_review_dir = planning_dir / "implementation" / "code_review"
    usage_path = planning_dir / "implementation" / "usage.md"
    compact = _markdown.is_lean_depth(_artifacts.planning_depth(planning_dir))

    if progress["state"] in {"invalid_index", "no_index"}:
        findings.append(_quality.finding("critical", "invalid-sections", "Cannot validate implementation without valid sections/index.md.", sections_dir / "index.md"))
        return _quality.emit_quality("implementation-state", findings, args)

    if not state_path.exists():
        findings.append(_quality.finding("high", "missing-state", "zagrosi_implement_state.json is missing.", state_path))
        completed: dict[str, Any] = {}
    else:
        state = _storage.load_json(state_path)
        completed = state.get("completed_sections", {})
        if not isinstance(completed, dict):
            findings.append(_quality.finding("critical", "invalid-state", "completed_sections must be an object.", state_path))
            completed = {}

    for section in progress["sections"]:
        if section not in completed:
            findings.append(_quality.finding("medium", "section-not-recorded", f"{section} is not recorded complete.", state_path))
            continue
        record = completed[section]
        if not record.get("completed_at"):
            findings.append(_quality.finding("low", "missing-completed-at", f"{section} has no completed_at timestamp.", state_path))
        if not compact and not record.get("commit"):
            findings.append(_quality.finding("low", "missing-commit", f"{section} has no commit recorded.", state_path))
        if compact:
            if record.get("review_status") not in {"pass", "fixed"}:
                findings.append(_quality.finding("high", "missing-review-status", f"{section} lacks a passing machine review status.", state_path))
            if not record.get("verification"):
                findings.append(_quality.finding("high", "missing-verification", f"{section} has no verification command recorded.", state_path))
        else:
            review_path = code_review_dir / f"{section}-review.md"
            diff_path = code_review_dir / f"{section}-diff.md"
            decisions_path = code_review_dir / f"{section}-decisions.md"
            if not review_path.exists():
                findings.append(_quality.finding("medium", "missing-review", f"Review file missing for {section}.", review_path))
            if not diff_path.exists():
                findings.append(_quality.finding("low", "missing-diff", f"Diff file missing for {section}.", diff_path))
            if not decisions_path.exists():
                findings.append(
                    _quality.finding(
                        "medium",
                        "missing-review-decisions",
                        f"Review decisions file missing for {section}.",
                        decisions_path,
                        "Write a decisions artifact that records accepted, rejected, and deferred review findings.",
                    )
                )
        if not compact:
            if "files_changed" in record and not record.get("files_changed"):
                findings.append(_quality.finding("low", "missing-file-evidence", f"{section} has no changed files recorded.", state_path))
            if "test_files" in record and not record.get("test_files"):
                findings.append(_quality.finding("low", "missing-test-evidence", f"{section} has no test files recorded.", state_path))
            if "review_artifacts" in record and not record.get("review_artifacts"):
                findings.append(_quality.finding("low", "missing-review-evidence", f"{section} has no review artifacts recorded.", state_path))

    if not compact and not usage_path.exists():
        findings.append(_quality.finding("medium", "missing-usage", "implementation/usage.md is missing.", usage_path))

    payload = _quality.quality_from_args(
        "implementation-state",
        findings,
        args,
        {
            "sections_dir": str(sections_dir),
            "state_path": str(state_path),
            "completed_sections": sorted(completed.keys()),
        },
    )
    return _quality.emit_payload(payload, args)
