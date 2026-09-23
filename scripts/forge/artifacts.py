"""Forge artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

from . import markdown as _markdown
from . import models as _models
from . import ownership as _ownership
from . import policy as _policy
from . import quality as _quality
from . import sections as _sections
from . import session as _session
from . import storage as _storage

def artifact(path: Path, names: list[str]) -> Path | None:
    for name in names:
        candidate = path / name
        if candidate.exists():
            return candidate
    return None


def planning_config(planning_dir: Path) -> dict[str, Any]:
    for name in ("zagrosi_plan_config.json", "deep_plan_config.json"):
        path = planning_dir / name
        if not path.exists():
            continue
        try:
            payload = _storage.load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def compact_plan_descriptor(planning_dir: Path) -> dict[str, Any] | None:
    return _session.cached_analysis("compact_plan", planning_dir, lambda observe: _compact_plan_descriptor(planning_dir, observe))


def _compact_plan_descriptor(planning_dir: Path, observe) -> dict[str, Any] | None:
    """Resolve the explicitly selected canonical plan; legacy files win."""
    for name in ("codex-plan.md", "claude-plan.md", "sections/index.md"):
        observe(planning_dir / name)
    physical = artifact(planning_dir, ["codex-plan.md", "claude-plan.md"])
    index = planning_dir / "sections" / "index.md"
    marker_path = physical or index
    if not marker_path.is_file():
        return None
    text = _storage.read_text(marker_path)
    meta, errors = _markdown.parse_forge_meta(text)
    if not (meta and meta.get("artifact_type") == "compact_plan"):
        raw = _markdown.extract_block(text, _policy.FORGE_META_START, "END_FORGE_META") or ""
        if not errors or "compact_plan" not in raw:
            return None
        meta = {}
    errors = list(errors)
    depth = meta.get("depth_mode")
    if not isinstance(depth, str):
        depth = None
    if depth not in _policy.DEPTH_MODES:
        errors.append("Compact plan must explicitly name a supported depth_mode.")
    progress = _sections.check_section_progress(planning_dir)
    sections = progress.get("sections", [])
    if progress.get("state") != "complete":
        errors.append("Compact plan requires a valid, complete section manifest.")
    if not physical and len(sections) != 1:
        errors.append("An index-owned compact plan requires exactly one section.")
    path = physical or (planning_dir / "sections" / f"{sections[0]}.md" if len(sections) == 1 else None)
    if marker_path.is_symlink() or (path and path.is_symlink()):
        errors.append("Compact plan files must be regular files, not symbolic links.")
    source: Path | None = None
    raw_source = meta.get("source")
    if not isinstance(raw_source, str) or not raw_source.strip() or Path(raw_source).is_absolute():
        errors.append("Compact plan source must be an explicit path relative to the planning directory.")
    else:
        try:
            candidate = observe(planning_dir / raw_source).resolve()
            if candidate == (path.resolve() if path else None) or candidate.is_relative_to((planning_dir / "sections").resolve()):
                errors.append("Compact plan source cannot be its plan, index, or a section.")
            elif not candidate.is_file() or candidate.suffix.lower() != ".md":
                errors.append("Compact plan source must name an existing Markdown file.")
            else:
                source_meta, _ = _markdown.parse_forge_meta(_storage.read_text(candidate))
                if source_meta and source_meta.get("artifact_type") == "compact_plan":
                    errors.append("Compact plan source must be a source spec, not another compact plan.")
                else:
                    source = candidate
        except (OSError, RuntimeError, ValueError):
            errors.append("Compact plan source cannot be resolved safely.")
    headings: dict[str, str] = {}
    if path and observe(path).is_file():
        for title, body in _markdown.markdown_h2_sections(_storage.read_text(path)):
            key = title.casefold()
            if key in headings:
                errors.append(f"Compact plan repeats the heading: {title}.")
            headings[key] = body.strip()
    return {"path": path, "marker_path": marker_path, "source": source, "depth_mode": depth, "headings": headings, "errors": errors}


def implementation_plan_path(planning_dir: Path) -> Path | None:
    physical = artifact(planning_dir, ["codex-plan.md", "claude-plan.md"])
    if physical:
        return physical
    compact = compact_plan_descriptor(planning_dir)
    return compact["path"] if compact and not compact["errors"] else None


def planning_artifact_text(planning_dir: Path, name: str, path: Path | None = None) -> str:
    path = path or planning_artifacts(planning_dir).get(name)
    if not path or not path.is_file():
        return ""
    compact = compact_plan_descriptor(planning_dir)
    if compact and path == compact["path"] and name in _policy.COMPACT_PLAN_HEADINGS:
        return compact["headings"].get(_policy.COMPACT_PLAN_HEADINGS[name], "")
    return _storage.read_text(path)


def compact_plan_findings(planning_dir: Path, *, depth: str | None = None, allow_compact: bool = True) -> list[_models.Finding]:
    compact = compact_plan_descriptor(planning_dir)
    if not compact:
        return []
    path = compact["path"] or compact["marker_path"]
    if not allow_compact:
        return [_quality.finding("critical", "compact-plan-not-supported", "Detached implementation requires the established physical plan and review artifacts.", path)]
    findings = [_quality.finding("high", "invalid-compact-plan", error, path) for error in compact["errors"]]
    if depth and depth != compact["depth_mode"] and not (_markdown.is_lean_depth(depth) and _markdown.is_lean_depth(compact["depth_mode"])):
        findings.append(_quality.finding("high", "compact-depth-mismatch", "Requested depth differs from the explicit compact plan depth.", path))
    if compact["errors"]:
        return findings
    headings = compact["headings"]
    for title in ("review", "tests first", "implementation contract"):
        if not headings.get(title):
            findings.append(_quality.finding("high", "incomplete-compact-plan", f"Compact plan needs a substantive {title} heading.", path))
    text = _storage.read_text(path)
    if not headings.get("owned files") or not _ownership.extract_section_owned_paths(text):
        findings.append(_quality.finding("high", "compact-plan-no-ownership", "Compact plan has no explicit owned files.", path))
    if not _markdown.has_verification(headings.get("tests first", "")):
        findings.append(_quality.finding("high", "compact-plan-no-tests", "Tests first needs a regression case and command, or justified inspection with an expected result.", path))
    if not _markdown.passing_review(headings.get("review", "")):
        findings.append(_quality.finding("high", "compact-review-incomplete", "Embedded review needs pass/fixed plus concrete Reviewed scope; blocked findings must be resolved.", path))
    return findings


def configured_source_spec(planning_dir: Path) -> Path | None:
    compact = compact_plan_descriptor(planning_dir)
    if compact and compact["source"] and not compact["errors"]:
        return compact["source"]
    configured = planning_config(planning_dir).get("initial_file")
    candidates: list[Path] = []
    if isinstance(configured, str) and configured.strip():
        candidate = Path(configured).expanduser()
        candidates.append(candidate if candidate.is_absolute() else planning_dir / candidate)
    candidates.extend([planning_dir / "spec.md", planning_dir / "requirements.md"])
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def requirement_source_spec(planning_dir: Path) -> Path | None:
    compact = compact_plan_descriptor(planning_dir)
    if compact and compact["source"] and not compact["errors"]:
        return compact["source"]
    return artifact(planning_dir, ["codex-spec.md", "claude-spec.md"]) or configured_source_spec(planning_dir)


def planning_depth(planning_dir: Path, fallback: str = _policy.DEFAULT_DEPTH) -> str:
    compact = compact_plan_descriptor(planning_dir)
    if compact and compact["depth_mode"] in _policy.DEPTH_MODES:
        return compact["depth_mode"]
    depth = planning_config(planning_dir).get("depth_mode")
    if isinstance(depth, str) and depth in _policy.DEPTH_MODES:
        return depth
    plan_path = artifact(planning_dir, ["codex-plan.md", "claude-plan.md"])
    if plan_path:
        meta, _ = _markdown.parse_forge_meta(_storage.read_text(plan_path))
        meta_depth = meta.get("depth_mode") if isinstance(meta, dict) else None
        if isinstance(meta_depth, str) and meta_depth in _policy.DEPTH_MODES:
            return meta_depth
    return fallback


def project_depth(planning_dir: Path, fallback: str = _policy.DEFAULT_DEPTH) -> str:
    for path in (planning_dir / ".zagrosi-project" / "session.json", planning_dir / ".deep-project" / "session.json"):
        if not path.exists():
            continue
        try:
            depth = _storage.load_json(path).get("depth_mode")
        except (OSError, ValueError, json.JSONDecodeError):
            return fallback
        return depth if isinstance(depth, str) and depth in _policy.DEPTH_MODES else fallback
    return fallback


def default_governance_files(planning_dir: Path, depth: str = _policy.DEFAULT_DEPTH) -> dict[str, Path]:
    return {
        "decisions": planning_dir / "decisions.md",
        "risks": planning_dir / "risk-register.md",
        "traceability": planning_dir / "traceability.md",
        "quality": planning_dir / "quality-gates.md",
    }


def write_if_missing(path: Path, content: str) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def governance_templates(depth: str) -> dict[str, str]:
    return {
        "decisions": (
            "# Decision Log\n\n"
            f"Depth mode: {depth}\n\n"
            "| ID | Date | Decision | Alternatives | Rationale | Impact |\n"
            "|----|------|----------|--------------|-----------|--------|\n"
            "| DEC-001 | TBD | TBD | TBD | TBD | TBD |\n"
        ),
        "risks": (
            "# Risk Register\n\n"
            "| ID | Risk | Severity | Likelihood | Mitigation | Section | Verification |\n"
            "|----|------|----------|------------|------------|---------|--------------|\n"
            "| RISK-001 | TBD | TBD | TBD | TBD | TBD | TBD |\n"
        ),
        "traceability": (
            "# Traceability Matrix\n\n"
            "| Requirement | Plan Coverage | Section Coverage | Test Coverage | Status |\n"
            "|-------------|---------------|------------------|---------------|--------|\n"
            "| REQ-001 | TBD | TBD | TBD | TBD |\n"
        ),
        "quality": (
            "# Quality Gates\n\n"
            "Run these before moving stages:\n\n"
            "- `lint-project-manifest`\n"
            "- `lint-plan`\n"
            "- `lint-sections`\n"
            "- `lint-implementation-state`\n"
            "- `traceability`\n"
        ),
    }


def interview_artifact(planning_dir: Path, phase: str) -> Path | None:
    return artifact(planning_dir, _policy.INTERVIEW_FILES[phase])


def has_interview_exchange(text: str) -> bool:
    has_question = re.search(r"(?im)^\s*(?:[-*]\s*)?(?:q|question)\s*[:|-]\s*\S", text) is not None
    has_answer = re.search(r"(?im)^\s*(?:[-*]\s*)?(?:a|answer)\s*[:|-]\s*\S", text) is not None
    has_table = bool(re.search(r"(?im)^\s*\|\s*(?:question|q)\s*\|\s*(?:answer|a|decision)", text))
    return (has_question and has_answer) or has_table


def nonempty_artifact(planning_dir: Path, names: list[str]) -> Path | None:
    path = artifact(planning_dir, names)
    if path and _storage.read_text(path).strip():
        return path
    return None


def plan_artifact_state(planning_dir: Path) -> dict[str, Path | None]:
    review_files = sorted((planning_dir / "reviews").glob("*.md")) if (planning_dir / "reviews").exists() else []
    state = {
        "research": nonempty_artifact(planning_dir, ["codex-research.md", "claude-research.md"]),
        "interview": nonempty_artifact(planning_dir, ["codex-interview.md", "claude-interview.md"]),
        "spec": nonempty_artifact(planning_dir, ["codex-spec.md", "claude-spec.md"]),
        "plan": nonempty_artifact(planning_dir, ["codex-plan.md", "claude-plan.md"]),
        "integration_notes": nonempty_artifact(
            planning_dir,
            ["codex-integration-notes.md", "claude-integration-notes.md"],
        ),
        "tdd": nonempty_artifact(planning_dir, ["codex-plan-tdd.md", "claude-plan-tdd.md"]),
        "review": next((path for path in review_files if _storage.read_text(path).strip()), None),
        "section_index": nonempty_artifact(planning_dir / "sections", ["index.md"]),
    }
    compact = compact_plan_descriptor(planning_dir)
    if compact and not compact["errors"]:
        paths = planning_artifacts(planning_dir)
        state.update({name: paths.get(name) for name in ("plan", "research", "tdd", "integration_notes", "review")})
        state["spec"] = compact["source"]
    return state


def plan_artifact_payload(state: dict[str, Path | None]) -> dict[str, str | None]:
    return {key: str(value) if value else None for key, value in state.items()}


def next_plan_action(
    artifacts: dict[str, Path | None],
    progress: dict[str, Any],
    config: dict[str, Any],
) -> str:
    if not artifacts["plan"]:
        return "write the canonical implementation plan"
    if config.get("review_mode") != "skip" and not artifacts.get("review"):
        return "review plan and record the verdict"
    if not artifacts["section_index"]:
        return "create concise sections/index.md"
    if progress.get("state") in {"has_index", "partial"}:
        return "write missing concise section files"
    if progress.get("state") == "complete":
        return "run zagrosi-implement"
    return "run plan postflight"


def planning_artifacts(planning_dir: Path) -> dict[str, Path | None]:
    paths = {
        "spec": artifact(planning_dir, ["codex-spec.md", "claude-spec.md"]),
        "research": artifact(planning_dir, ["codex-research.md", "claude-research.md"]),
        "interview": artifact(planning_dir, ["codex-interview.md", "claude-interview.md"]),
        "plan": implementation_plan_path(planning_dir),
        "integration_notes": artifact(planning_dir, ["codex-integration-notes.md", "claude-integration-notes.md"]),
        "tdd": artifact(planning_dir, ["codex-plan-tdd.md", "claude-plan-tdd.md"]),
        "evidence": planning_dir / "codex-evidence.md",
        "decisions": planning_dir / "decisions.md",
        "risks": planning_dir / "risk-register.md",
        "traceability": planning_dir / "traceability.md",
        "quality": planning_dir / "quality-gates.md",
    }
    compact = compact_plan_descriptor(planning_dir)
    if compact and not compact["errors"]:
        for name, heading in _policy.COMPACT_PLAN_HEADINGS.items():
            if compact["headings"].get(heading) and not (paths.get(name) and paths[name].exists()):
                paths[name] = compact["path"]
    return paths


def existing_artifact_texts(planning_dir: Path) -> dict[str, str]:
    return {
        name: planning_artifact_text(planning_dir, name, path)
        for name, path in planning_artifacts(planning_dir).items()
        if path and path.exists()
    }
