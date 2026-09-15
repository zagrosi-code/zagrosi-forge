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
    if not errors:
        _, dependency_errors = section_dependency_analysis(text, sections)
        errors.extend(dependency_errors)
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


def section_dependency_analysis(index_text: str, sections: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    known = set(sections)
    dependencies = {section: [] for section in sections}
    errors: list[str] = []

    def add_dependencies(dependent: str, predecessors: str) -> None:
        candidates = set(_policy.SECTION_TOKEN_RE.findall(dependent))
        if len(candidates) != 1 or not candidates <= known:
            errors.append(f"Dependency row must name exactly one known section: {dependent!r}")
            return
        section = candidates.pop()
        references = list(_policy.SECTION_TOKEN_RE.finditer(predecessors))
        deps = [match[0] for match in references]
        starts = {match.start() for match in references}
        if any(match.start() not in starts for match in re.finditer(r"\bsection-", predecessors)):
            errors.append(f"Malformed predecessor reference for {section}: {predecessors!r}")
        if not deps and predecessors.strip("`* .").lower() not in {"", "none", "-", "—", "n/a", "no dependencies", "independent"}:
            errors.append(f"Unrecognized dependencies for {section}: {predecessors!r}")
        if section in deps:
            errors.append(f"Section cannot depend on itself: {section}")
        current = dependencies.setdefault(section, [])
        for dep in deps:
            if dep not in current:
                current.append(dep)

    blocks, lines = _markdown.split_markdown_fences_with_closure(index_text)
    if any(not closed for _, _, closed in blocks):
        errors.append("Unclosed Markdown fence in sections index.")
    dependency_column: int | None = 1
    dependency_table = False
    for line in lines:
        stripped = line.strip()
        if "|" in stripped:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            labels = [cell.strip("`* ").lower() for cell in cells]
            if labels[0] == "section":
                incoming_label = r"(?:depends on|dependenc(?:y|ies)|predecessors?|after|requires)\b"
                outgoing_label = r"(?:blocks|dependents?|dependants?|successors?)\b"
                relationship = rf"\b(?:depends\b|{incoming_label}|{outgoing_label})"
                columns = {i for i, label in enumerate(labels) if re.search(relationship, label)}
                incoming = [i for i in columns if re.match(incoming_label, labels[i])
                            and not re.search(rf"\b{outgoing_label}", labels[i])]
                outgoing = {i for i in columns if re.match(outgoing_label, labels[i])
                            and not re.search(rf"\b(?:depends\b|{incoming_label})", labels[i])}
                ambiguous = bool(columns) and (len(incoming) != 1 or bool(columns - set(incoming) - outgoing))
                if ambiguous:
                    errors.append(f"Ambiguous dependency table header: {stripped!r}")
                dependency_column = incoming[0] if len(incoming) == 1 and not ambiguous else None
                dependency_table = dependency_column is not None
                continue
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            if dependency_column is not None and (dependency_table or _policy.SECTION_TOKEN_RE.search(cells[0])):
                if len(cells) <= dependency_column:
                    errors.append(f"Dependency row has no predecessor cell: {stripped!r}")
                else:
                    add_dependencies(cells[0], cells[dependency_column])
            continue

        dependency_column, dependency_table = 1, False
        lower = stripped.lower()
        if "depends on" not in lower:
            continue
        before, after = re.split(r"\bdepends on\b", stripped, maxsplit=1, flags=re.IGNORECASE)
        if not _policy.SECTION_TOKEN_RE.search(before):
            continue
        add_dependencies(before, after)
    return dependencies, errors


def parse_section_dependencies(index_text: str, sections: list[str]) -> dict[str, list[str]]:
    return section_dependency_analysis(index_text, sections)[0]


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
