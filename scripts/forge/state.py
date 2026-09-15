"""Forge state."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import re

from . import markdown as _markdown
from . import models as _models
from . import output as _output
from . import policy as _policy
from . import quality as _quality
from . import sections as _sections
from . import storage as _storage

def completed_sections(planning_dir: Path, state: dict[str, Any] | None = None) -> set[str]:
    state = load_implementation_state(planning_dir) if state is None else state
    completed = state.get("completed_sections", {})
    if not isinstance(completed, dict):
        return set()
    return {
        section for section, record in completed.items()
        if section not in state.get("pending_sections", {})
        and not completion_evidence_findings(planning_dir, section, record)
    }


def implementation_recording_status(planning_dir: Path, state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = load_implementation_state(planning_dir) if state is None else state
    progress = _sections.check_section_progress(planning_dir)
    sections = progress.get("sections", []) if progress.get("state") not in {"invalid_index", "no_index"} else []
    known_sections = set(sections)
    recorded = completed_sections(planning_dir, state)
    recorded_known = sorted(recorded & known_sections)
    remaining = [section for section in sections if section not in recorded]
    pending = sorted(state.get("pending_sections", {}))
    raw_completed = state.get("completed_sections", {})
    invalid = sorted(set(raw_completed) - recorded - set(pending)) if isinstance(raw_completed, dict) else []
    sections_recorded_complete = bool(sections) and not remaining and not pending and progress.get("state") == "complete"
    if sections_recorded_complete:
        recording_state = "complete"
    elif pending:
        recording_state = "pending"
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
        "pending_sections": pending,
        "invalid_completed_sections": invalid,
        "unknown_recorded_sections": sorted(recorded - known_sections),
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


def completion_evidence_findings(planning_dir: Path, section: str, record: Any) -> list[_models.Finding]:
    """Use the same evidence contract for new records, legacy records, and readiness."""
    path = implementation_state_path(planning_dir)
    if not _policy.SECTION_RE.fullmatch(section) or not isinstance(record, dict):
        return [_quality.finding("high", "invalid-completion-record", f"{section} completion must be an object.", path)]
    review_path = planning_dir / "implementation" / "code_review" / f"{section}-review.md"
    legacy_text = ""
    if not record.get("review_status") or not record.get("verification"):
        if review_path.is_file():
            _, lines = _markdown.split_markdown_fences_with_closure(_storage.read_text(review_path))
            legacy_text = "\n".join(lines)
    status = record.get("review_status")
    if status is None:
        verdicts = re.findall(r"(?im)^Verdict:[ \t]*(\w+)\b", legacy_text)
        reviewed = re.search(r"(?im)^Reviewed:[ \t]*(?!none\b|tbd\b|pending\b|n/a\b)\S.+", legacy_text)
        passing_review = bool(
            verdicts and all(value.lower() in {"pass", "fixed"} for value in verdicts) and reviewed
            or not verdicts and re.search(r"(?im)^\s*No (?:blocking|material) findings[.!]?\s*$", legacy_text)
        )
    else:
        passing_review = isinstance(status, str) and status in {"pass", "fixed"}
    verification = record.get("verification")
    if isinstance(verification, str):
        verification = [verification]
    verified = isinstance(verification, list) and any(
        isinstance(value, str) and value.strip().strip("`*. ").lower() not in {"", "none", "n/a", "tbd", "todo", "pending"}
        for value in verification
    )
    if not verified:
        verified = bool(re.search(
            r"(?im)^(?:Verification|Verified|Tests):[ \t]*`[^`]+`[ \t:—-]*(?:passed|successful)\b(?![^\n]*\b(?:failed|blocked|pending)\b)[^\n]*$",
            legacy_text,
        ))
    findings = []
    if not passing_review:
        findings.append(_quality.finding("high", "missing-review-status", f"{section} lacks a passing review verdict.", path))
    if not verified:
        findings.append(_quality.finding("high", "missing-verification", f"{section} has no verification evidence recorded.", path))
    return findings


def implementation_state_analysis(
    planning_dir: Path, state: dict[str, Any] | None = None,
) -> tuple[list[_models.Finding], dict[str, Any]]:
    findings: list[_models.Finding] = []
    progress = _sections.check_section_progress(planning_dir)
    state_path = implementation_state_path(planning_dir)
    if progress["state"] in {"invalid_index", "no_index"}:
        findings.append(_quality.finding("critical", "invalid-sections", "Cannot validate implementation without valid sections/index.md.", planning_dir / "sections" / "index.md"))
        return findings, {}
    if state is None:
        if not state_path.exists():
            findings.append(_quality.finding("high", "missing-state", "zagrosi_implement_state.json is missing.", state_path))
        state = load_implementation_state(planning_dir)
    completed = state.get("completed_sections", {})
    if not isinstance(completed, dict):
        findings.append(_quality.finding("critical", "invalid-state", "completed_sections must be an object.", state_path))
        completed = {}
    for section in progress["sections"]:
        if section not in completed:
            findings.append(_quality.finding("medium", "section-not-recorded", f"{section} is not recorded complete.", state_path))
            continue
        record = completed[section]
        findings.extend(completion_evidence_findings(planning_dir, section, record))
        if isinstance(record, dict) and not record.get("completed_at"):
            findings.append(_quality.finding("low", "missing-completed-at", f"{section} has no completed_at timestamp.", state_path))
    pending = sorted(state.get("pending_sections", {}))
    if pending:
        findings.append(_quality.finding("high", "pending-completion", f"Completion checks must be retried for: {', '.join(pending)}.", state_path))
    return findings, {
        "sections_dir": str(planning_dir / "sections"),
        "state_path": str(state_path),
        "completed_sections": sorted(completed),
        "pending_sections": pending,
    }


def lint_implementation_state(args: argparse.Namespace) -> int:
    planning_dir = _storage.resolve_path(args.sections_dir).parent
    findings, extras = implementation_state_analysis(planning_dir)
    return _quality.emit_quality("implementation-state", findings, args, extras)
