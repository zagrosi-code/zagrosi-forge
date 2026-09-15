"""Forge sections."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import re

from . import markdown as _markdown
from . import ownership as _ownership
from . import policy as _policy
from . import storage as _storage

def check_section_progress(planning_dir: Path) -> dict[str, Any]:
    sections_dir = planning_dir / "sections"
    index_path = sections_dir / "index.md"
    if not index_path.exists():
        return {"state": "no_index", "sections_dir": str(sections_dir)}

    text = _storage.read_text(index_path)
    config, config_errors = _markdown.parse_project_config(text)
    sections, manifest_errors = _markdown.parse_numbered_manifest(text, "SECTION_MANIFEST", _policy.SECTION_RE, prefix="section-")
    errors = config_errors + manifest_errors
    if errors:
        return {"state": "invalid_index", "sections_dir": str(sections_dir), "errors": errors}

    missing: list[str] = []
    empty: list[str] = []
    complete: list[str] = []
    for section in sections:
        path = sections_dir / f"{section}.md"
        if not path.exists():
            missing.append(section)
        elif not _storage.read_text(path).strip():
            empty.append(section)
        else:
            complete.append(section)

    if not sections:
        state = "invalid_index"
    elif len(complete) == len(sections):
        state = "complete"
    elif complete or empty:
        state = "partial"
    else:
        state = "has_index"

    return {
        "state": state,
        "sections_dir": str(sections_dir),
        "project_config": config,
        "sections": sections,
        "complete": complete,
        "missing": missing,
        "empty": empty,
        "progress": f"{len(complete)}/{len(sections)}",
        "next_section": (missing + empty)[0] if (missing + empty) else None,
    }


def parse_section_dependencies(index_text: str, sections: list[str]) -> dict[str, list[str]]:
    known = set(sections)
    dependencies = {section: [] for section in sections}

    def add_dependencies(section: str, deps: list[str]) -> None:
        if section not in known:
            return
        current = dependencies.setdefault(section, [])
        for dep in deps:
            if dep != section and dep not in current:
                current.append(dep)

    for line in index_text.splitlines():
        stripped = line.strip()
        if "|" in stripped:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and cells[0] in known:
                depends_cell = cells[1] if len(cells) > 1 else ""
                add_dependencies(cells[0], _policy.SECTION_TOKEN_RE.findall(depends_cell))
            continue

        lower = stripped.lower()
        if "depends on" not in lower:
            continue
        before, after = re.split(r"\bdepends on\b", stripped, maxsplit=1, flags=re.IGNORECASE)
        dependent_candidates = _policy.SECTION_TOKEN_RE.findall(before)
        if not dependent_candidates:
            continue
        add_dependencies(dependent_candidates[0], _policy.SECTION_TOKEN_RE.findall(after))
    return dependencies


def transitive_section_predecessors(
    section: str,
    dependencies: dict[str, list[str]],
) -> set[str]:
    predecessors: set[str] = set()
    pending = list(dependencies.get(section, []))
    while pending:
        predecessor = pending.pop()
        if predecessor in predecessors:
            continue
        predecessors.add(predecessor)
        pending.extend(dependencies.get(predecessor, []))
    return predecessors


def dependency_graph(planning_dir: Path, progress: dict[str, Any] | None = None) -> dict[str, list[str]]:
    progress = progress or check_section_progress(planning_dir)
    if progress.get("state") in {"invalid_index", "no_index"}:
        return {}
    index_path = planning_dir / "sections" / "index.md"
    return parse_section_dependencies(_storage.read_text(index_path), progress.get("sections", []))


def section_metrics(section: str, path: Path, dependencies: dict[str, list[str]]) -> dict[str, Any]:
    text = _storage.read_text(path) if path.exists() else ""
    files = _ownership.extract_section_owned_paths(text)
    words = _markdown.word_count(text)
    dep_count = len(dependencies.get(section, []))
    risk_terms = ["security", "privacy", "auth", "permission", "migration", "data", "payment", "token", "secret"]
    risk_points = dep_count + (2 if _markdown.contains_any(text, risk_terms) else 0) + (1 if len(files) > 5 else 0)
    effort_score = words + len(files) * 120 + dep_count * 180
    effort = "large" if effort_score >= 1800 else "medium" if effort_score >= 750 else "small"
    risk = "high" if risk_points >= 4 else "medium" if risk_points >= 2 else "low"
    return {
        "section": section,
        "path": str(path),
        "word_count": words,
        "file_count": len(files),
        "files": files,
        "dependency_count": dep_count,
        "dependencies": dependencies.get(section, []),
        "effort": effort,
        "risk": risk,
    }


def ready_sections(progress: dict[str, Any], dependencies: dict[str, list[str]], completed: set[str]) -> list[str]:
    sections = progress.get("sections", [])
    ready: list[str] = []
    for section in sections:
        if section in completed:
            continue
        if all(dep in completed for dep in dependencies.get(section, [])):
            ready.append(section)
    return ready
